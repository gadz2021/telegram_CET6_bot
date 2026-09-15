import asyncio
import os
import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import database
import handlers
from handlers import _send_recall_content, _get_user_recall_lock

async def test_concurrent_recall_prevention():
    print("=== Testing Concurrent Recall Prevention ===")
    await database.init_db()
    test_uid = 88888888
    
    # Initialize user state in DB
    await database.add_to_whitelist(test_uid)
    await database.update_vocab_progress(test_uid, 0)
    await database.set_pending_eval_word(test_uid, None)
    await database.set_pending_quiz_word(test_uid, None)
    async with database.aiosqlite.connect(database.DB_PATH) as db:
        await db.execute("DELETE FROM word_schedule WHERE user_id = ?", (test_uid,))
        await db.commit()

    # Mock context
    mock_bot = AsyncMock()
    mock_context = MagicMock()
    mock_context.bot = mock_bot
    
    # Mock nvidia client to simulate 0.5s network delay
    mock_nvidia = MagicMock()
    async def slow_chat(*args, **kwargs):
        await asyncio.sleep(0.3)
        return "📖 **test** /test/\n释义测试\n\n1️⃣ Example\n翻译\n\n2️⃣ Example\n翻译\n\n3️⃣ Example\n翻译\n\n📝 考试要点\n考点"
    mock_nvidia.chat = slow_chat
    mock_context.bot_data = {"nvidia": mock_nvidia}

    # Verify initial lock state
    lock = _get_user_recall_lock(test_uid)
    assert not lock.locked()

    # Launch 5 concurrent calls to _send_recall_content
    results = await asyncio.gather(
        _send_recall_content(mock_context, test_uid, test_uid),
        _send_recall_content(mock_context, test_uid, test_uid),
        _send_recall_content(mock_context, test_uid, test_uid),
        _send_recall_content(mock_context, test_uid, test_uid),
        _send_recall_content(mock_context, test_uid, test_uid),
    )

    print(f"Results of 5 concurrent calls: {results}")
    
    # Exactly ONE call should succeed (True), others rejected (False)
    success_count = sum(1 for r in results if r is True)
    rejected_count = sum(1 for r in results if r is False)
    
    print(f"Success: {success_count}, Rejected: {rejected_count}")
    assert success_count == 1, f"Expected exactly 1 success, got {success_count}"
    assert rejected_count == 4, f"Expected 4 rejections, got {rejected_count}"

    # Verify that Telegram send_message was only called once
    print(f"bot.send_message call count: {mock_bot.send_message.call_count}")
    assert mock_bot.send_message.call_count == 1, "send_message should only be called once!"

    # Verify progress in DB is exactly 1 (not 5)
    p = await database.get_vocab_progress(test_uid)
    print(f"Vocab progress index: {p}")
    assert p == 1, f"Expected vocab progress 1, got {p}"

    # Verify pending_eval_word is set
    pending = await database.get_pending_eval_word(test_uid)
    print(f"Pending evaluation word: {pending}")
    assert pending is not None

    # Now test subsequent call when pending_eval_word is set:
    # It should block and send reminder instead of new word
    mock_bot.send_message.reset_mock()
    res = await _send_recall_content(mock_context, test_uid, test_uid)
    assert res is True
    call_args = mock_bot.send_message.call_args[1]
    assert "Active Recall 自评打卡提醒" in call_args["text"]

    print("🎉 Concurrent recall prevention test PASSED!")

async def test_reminder_callback_dedup():
    print("\n=== Testing Reminder Callback Deduplication ===")
    test_uid = 77777777
    await database.add_to_whitelist(test_uid)
    await database.update_vocab_progress(test_uid, 0)
    await database.set_pending_eval_word(test_uid, "cement")

    # Mock query and context
    mock_query = AsyncMock()
    mock_query.from_user.id = test_uid
    mock_query.data = "rgr:cement:good"
    mock_msg = MagicMock()
    mock_msg.chat_id = test_uid
    mock_msg.text = "自评打卡提醒"
    # Old keyboard with reminder buttons
    btn = MagicMock()
    btn.callback_data = "rgr:cement:good"
    mock_msg.reply_markup.inline_keyboard = [[btn]]
    mock_query.message = mock_msg

    mock_update = MagicMock()
    mock_update.callback_query = mock_query

    mock_bot = AsyncMock()
    mock_context = MagicMock()
    mock_context.bot = mock_bot
    mock_nvidia = MagicMock()
    async def fast_chat(*args, **kwargs):
        return "📖 **revolutionize** /rev/\n测试释义\n\n1️⃣ Ex\n译\n\n2️⃣ Ex\n译\n\n3️⃣ Ex\n译\n\n📝 考点\n点"
    mock_nvidia.chat = fast_chat
    mock_context.bot_data = {"nvidia": mock_nvidia}

    # First call: from reminder
    await handlers.callback_review_grade(mock_update, mock_context)
    
    # Check that edit_message_reply_markup was called, and the new keyboard DOES NOT contain recall_next
    edit_call_args = mock_query.edit_message_reply_markup.call_args[1]
    new_kb = edit_call_args["reply_markup"].inline_keyboard
    all_callbacks = [b.callback_data for row in new_kb for b in row]
    print(f"Updated buttons on reminder card: {all_callbacks}")
    assert "recall_next" not in all_callbacks, "Reminder card must NOT have recall_next button!"
    assert any("noop" in cb for cb in all_callbacks)

    # Check that pending_eval_word was cleared and then set to next word
    pending = await database.get_pending_eval_word(test_uid)
    print(f"New pending word after reminder completion: {pending}")
    assert pending == "determine" or pending is not None

    # Now simulate a second duplicate tap on the SAME reminder card
    mock_bot.send_message.reset_mock()
    await handlers.callback_review_grade(mock_update, mock_context)
    # The second call must NOT trigger another _send_recall_content
    print(f"send_message call count on duplicate reminder tap: {mock_bot.send_message.call_count}")
    assert mock_bot.send_message.call_count == 0, "Duplicate reminder tap must NOT trigger send_recall_content!"

    print("🎉 Reminder deduplication test PASSED!")

if __name__ == "__main__":
    asyncio.run(test_concurrent_recall_prevention())
    asyncio.run(test_reminder_callback_dedup())

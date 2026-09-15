import asyncio
import os
import json
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

import database
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEFAULT_MODEL
from rate_limiter import RateLimiter
from nvidia_client import NvidiaClient
import handlers

async def test_all():
    print("=== 1. Testing Database 4-Tier & Slash/Defer Functions ===")
    await database.init_db()
    test_uid = 999111222
    await database.add_to_whitelist(test_uid)

    # Test daily progress
    cnt1 = await database.record_daily_new_word(test_uid)
    cnt2 = await database.record_daily_new_word(test_uid)
    today_cnt = await database.get_today_new_word_count(test_uid)
    print(f"Daily progress recorded: {cnt1}, {cnt2}, queried today count: {today_cnt}")
    assert today_cnt >= 2, "Daily new word count should be at least 2"

    # Test slash word
    await database.slash_word(test_uid, "water")
    slashed_cnt = await database.get_slashed_words_count(test_uid)
    print(f"Slashed count after slashing 'water': {slashed_cnt}")
    assert slashed_cnt >= 1, "Slashed count should be at least 1"

    # Verify that get_due_review_word excludes slashed words
    due = await database.get_due_review_word(test_uid)
    if due:
        assert due["word"] != "water", "Slashed word should NOT be returned in due reviews"

    # Test unslash word
    await database.unslash_word(test_uid, "water")
    slashed_cnt_after = await database.get_slashed_words_count(test_uid)
    print(f"Slashed count after unslashing 'water': {slashed_cnt_after}")
    assert slashed_cnt_after == slashed_cnt - 1, "Slashed count should decrease by 1"

    # Test defer word
    await database.defer_word(test_uid, "archaeopteryx")
    def_words = await database.get_deferred_words(test_uid)
    def_cnt = await database.get_deferred_words_count(test_uid)
    print(f"Deferred words: {def_words}, count: {def_cnt}")
    assert "archaeopteryx" in def_words
    assert def_cnt >= 1

    # Test stats
    stats = await database.get_stats(test_uid)
    print("User stats summary:", {k: stats[k] for k in ["slashed_count", "deferred_count", "today_new_count"]})
    assert "slashed_count" in stats
    assert "deferred_count" in stats
    assert "today_new_count" in stats
    print("✅ Database functions PASSED!")

    print("\n=== 2. Testing DeepSeek API Generation with 4-Tier Prompt ===")
    rate_limiter = RateLimiter(60)
    client = NvidiaClient(rate_limiter)

    # Test high-frequency core word: subsequent
    print("Generating explanation for 'subsequent'...")
    start_t = datetime.now()
    exp_subsequent = await handlers._get_or_generate_word_explanation(client, test_uid, "subsequent")
    duration = (datetime.now() - start_t).total_seconds()
    print(f"Generated in {duration:.2f}s:\n{exp_subsequent[:250]}...\n")

    # Verify 4-tier tags
    valid_tags = ["【六级高频核心词", "【基础必会词", "【冲刺拔高词", "【低频生僻词"]
    has_valid_tag = any(t in exp_subsequent for t in valid_tags)
    assert has_valid_tag, f"Explanation must contain one of 4-tier tags! Found: {exp_subsequent[:100]}"
    assert "subsequent" in exp_subsequent.lower()
    assert "/" in exp_subsequent, "Should contain phonetic symbol /.../"
    assert "1️⃣" in exp_subsequent and "2️⃣" in exp_subsequent, "Should contain numbered examples"
    print(f"✅ Tag check passed! Found tag: {[t for t in valid_tags if t in exp_subsequent][0]}")

    # Test elementary / fundamental word: display
    print("\nGenerating explanation for fundamental word 'display'...")
    exp_display = await handlers._get_or_generate_word_explanation(client, test_uid, "display")
    print(f"Display explanation first 100 chars:\n{exp_display[:120]}...\n")
    assert any(t in exp_display for t in valid_tags)
    print("✅ Elementary word explanation generated successfully with tag!")

    print("\n=== 3. Testing Callback Handlers: slash_word, unslash_word, defer_word ===")
    mock_update = MagicMock()
    mock_query = AsyncMock()
    mock_msg = AsyncMock()
    mock_msg.chat_id = 12345
    mock_msg.reply_markup = MagicMock()
    mock_msg.reply_markup.inline_keyboard = [
        [MagicMock(callback_data="slash:cement")],
        [MagicMock(callback_data="rg:cement:good")]
    ]
    mock_query.message = mock_msg
    mock_query.from_user.id = test_uid
    mock_update.callback_query = mock_query

    mock_context = MagicMock()
    mock_context.bot_data = {"nvidia": client}
    mock_context.bot.send_message = AsyncMock()

    # Test callback_slash_word
    mock_query.data = "slash:cement"
    await handlers.callback_slash_word(mock_update, mock_context)
    assert mock_query.answer.called
    print("Slash callback answered:", mock_query.answer.call_args[0])
    assert mock_query.edit_message_reply_markup.called
    new_kb = mock_query.edit_message_reply_markup.call_args[1]["reply_markup"].inline_keyboard
    callbacks = [btn.callback_data for row in new_kb for btn in row]
    print("Updated buttons after slash:", callbacks)
    assert "unslash:cement" in callbacks, "Must provide undo slash button!"

    # Test callback_unslash_word
    mock_query.data = "unslash:cement"
    await handlers.callback_unslash_word(mock_update, mock_context)
    assert mock_query.answer.called
    print("Unslash callback answered:", mock_query.answer.call_args[0])
    assert mock_query.edit_message_reply_markup.called
    new_kb2 = mock_query.edit_message_reply_markup.call_args[1]["reply_markup"].inline_keyboard
    callbacks2 = [btn.callback_data for row in new_kb2 for btn in row]
    print("Updated buttons after unslash:", callbacks2)
    assert "slash:cement" in callbacks2, "Must restore slash button after unslashing!"

    # Test callback_defer_word
    mock_query.data = "defer:obscure"
    await handlers.callback_defer_word(mock_update, mock_context)
    assert mock_query.answer.called
    print("Defer callback answered:", mock_query.answer.call_args[0])
    def_list = await database.get_deferred_words(test_uid)
    assert "obscure" in def_list, "Deferred word must be saved in database"

    print("\n🎉 ALL 4-TIER, SLASH, UNSLASH, DEFER AND PROGRESS TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(test_all())

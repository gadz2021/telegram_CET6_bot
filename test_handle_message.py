import asyncio
import database
import handlers
from unittest.mock import AsyncMock, MagicMock

async def test_handle_message_quiz_and_chat():
    print("=== Testing handle_message for quiz response and normal chat ===")
    await database.init_db()
    test_uid = 5019646314  # Admin user ID
    
    # 1. Setup mock update and context
    mock_user = MagicMock()
    mock_user.id = test_uid
    mock_user.first_name = "TestUser"
    
    mock_msg = MagicMock()
    mock_msg.text = "cement means 水泥 or 巩固"
    mock_msg.reply_text = AsyncMock()
    mock_msg.chat = MagicMock()
    mock_msg.chat.send_action = AsyncMock()

    mock_update = MagicMock()
    mock_update.effective_user = mock_user
    mock_update.message = mock_msg

    # Mock client
    mock_nvidia = AsyncMock()
    mock_nvidia.get_default_model.return_value = "deepseek-flash"
    mock_nvidia.is_model_available.return_value = True
    mock_nvidia.chat.return_value = "太棒啦！回答非常准确，cement 作为名词表示水泥，作为动词表示巩固！"

    mock_context = MagicMock()
    mock_context.bot_data = {"nvidia": mock_nvidia}

    # Case 1: Testing Quiz Word evaluation (The exact case that failed with UnboundLocalError!)
    print("\n--- Testing Case 1: Quiz Word Evaluation ---")
    await database.set_pending_quiz_word(test_uid, "cement")
    
    # Call handle_message
    await handlers.handle_message(mock_update, mock_context)
    
    # Check that reply_text was called with evaluation
    assert mock_msg.reply_text.called, "reply_text should have been called for quiz evaluation!"
    reply_call_args = mock_msg.reply_text.call_args[0]
    reply_kwargs = mock_msg.reply_text.call_args[1]
    print(f"Quiz Evaluation Reply: {reply_call_args[0][:80]}...")
    assert "外教闪测点评" in reply_call_args[0]
    assert "cement" in reply_call_args[0]
    assert "已自动为你打卡记录掌握" in reply_call_args[0], "Should automatically record mastery!"

    # Verify buttons on evaluation message include recall_next (no scrolling up!)
    kb = reply_kwargs["reply_markup"].inline_keyboard
    callbacks = [b.callback_data for row in kb for b in row]
    print(f"Evaluation message buttons: {callbacks}")
    assert "recall_next" in callbacks, "Must include recall_next button so user doesn't have to scroll up!"

    # Check pending_quiz_word is cleared
    p_quiz = await database.get_pending_quiz_word(test_uid)
    assert p_quiz is None, "pending_quiz_word should be cleared!"

    # Case 2: Testing Normal Chat
    print("\n--- Testing Case 2: Normal Chat ---")
    mock_msg.reply_text.reset_mock()
    mock_msg.text = "Can you help me practice CET-6 writing?"
    
    await handlers.handle_message(mock_update, mock_context)
    assert mock_msg.reply_text.called, "reply_text should have been called for normal chat!"
    chat_reply = mock_msg.reply_text.call_args[0][0]
    print(f"Normal Chat Reply: {chat_reply[:80]}...")

    print("\n🎉 ALL handle_message tests PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(test_handle_message_quiz_and_chat())

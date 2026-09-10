import asyncio
import time
import logging
import database
import config
from rate_limiter import RateLimiter
from nvidia_client import NvidiaClient
from handlers import _get_or_generate_word_explanation, _chat_with_auto_failover

logging.basicConfig(level=logging.INFO)

async def test_all():
    print("=== 1. Testing Database Init & Migration ===")
    await database.init_db()
    
    # Check default user model
    user_id = 5019646314
    model = await database.get_user_model(user_id, config.DEFAULT_MODEL)
    print(f"User {user_id} current model in DB: {model}")
    assert model in ["deepseek-flash", "deepseek-v4-pro"], f"Expected deepseek model, got {model}"

    print("\n=== 2. Testing DeepSeek Client ===")
    limiter = RateLimiter(60)
    client = NvidiaClient(limiter)
    
    # Check models list
    models = await client.fetch_models()
    print(f"Available models count: {len(models)}")
    for m in models:
        print(f" - {m['id']} (speed: {m.get('speed')}s)")
    assert any(m["id"] == "deepseek-flash" for m in models)

    print("\n=== 3. Testing Chat Completion Speed ===")
    t0 = time.time()
    resp = await client.chat("deepseek-flash", [{"role": "user", "content": "Say 'hello CET-6' and nothing else."}])
    t_chat = time.time() - t0
    print(f"Chat response in {t_chat:.2f}s: {resp}")
    assert len(resp) > 0

    print("\n=== 4. Testing _chat_with_auto_failover ===")
    t0 = time.time()
    failover_resp = await _chat_with_auto_failover(client, user_id, [{"role": "user", "content": "What is CET-6?"}])
    t_failover = time.time() - t0
    print(f"Failover response in {t_failover:.2f}s: {failover_resp[:60]}...")

    print("\n=== 5. Testing _get_or_generate_word_explanation & Cache ===")
    # First call: triggers DeepSeek generation
    t0 = time.time()
    word = "subsequent"
    exp1 = await _get_or_generate_word_explanation(client, user_id, word)
    t_gen = time.time() - t0
    print(f"Word explanation generated in {t_gen:.2f}s:\n{exp1}")
    assert "subsequent" in exp1.lower()
    assert "/" in exp1 or "[" in exp1, "Should contain phonetic symbol!"

    # Second call: should hit SQLite cache instantly (< 0.05s)
    t0 = time.time()
    exp2 = await _get_or_generate_word_explanation(client, user_id, word)
    t_cache = time.time() - t0
    print(f"Word explanation retrieved from cache in {t_cache:.4f}s")
    assert exp1 == exp2
    assert t_cache < 0.1, f"Cache retrieval too slow: {t_cache}"

    await client.close()
    print("\n🎉 ALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(test_all())

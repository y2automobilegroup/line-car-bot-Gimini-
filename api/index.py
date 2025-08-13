import os
import logging
import re
import asyncio
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks, Depends
from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from supabase import create_async_client, AsyncClient
from openai import AsyncOpenAI
from datetime import datetime, timedelta, timezone

# --- 環境變數設定 ---
# (新增) SECRET_KEY 用於保護內部 API 的安全
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "your-strong-secret-key")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
# ... 其他環境變數 ...

# --- 初始化 ---
# ... (與前一版相同) ...

# (升級) 建立 Supabase Client
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase: AsyncClient = create_async_client(supabase_url, supabase_key)

# (升級) 檢查 API 金鑰的依賴項
def get_admin_key(request: Request):
    api_key = request.headers.get("X-Admin-API-Key")
    if api_key != ADMIN_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid Admin API Key")
    return api_key

# --- 核心功能 (包含狀態檢查) ---

async def get_chat_mode(user_id: str) -> str:
    """從 Supabase 獲取指定用戶的聊天模式"""
    try:
        response = await supabase.table("chat_states").select("mode").eq("user_id", user_id).single().execute()
        return response.data.get("mode", "ai")
    except Exception:
        # 若找不到用戶資料，預設為 'ai' 模式
        return "ai"

# ... (handle_user_query, format_car_details 等函式維持不變) ...

# --- (升級) 背景訊息處理 (加入模式檢查) ---
async def process_text_message(event: MessageEvent):
    user_id = event.source.user_id
    user_text = event.message.text
    reply_token = event.reply_token

    # **關鍵**：在處理訊息前回覆前，先檢查聊天模式
    current_mode = await get_chat_mode(user_id)
    
    if current_mode == 'human':
        logger.info(f"使用者 {user_id} 處於 'human' 模式，AI 忽略此訊息。")
        return # 直接結束，不回覆

    logger.info(f"使用者 {user_id} 處於 'ai' 模式，開始處理訊息: {user_text}")
    
    reply_text = await handle_user_query(user_text)
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=reply_text)])
        )
    logger.info(f"已回覆使用者 {user_id}")


# --- (新增) 供給真人客服與定時任務使用的 API ---

@app.post("/admin/switch_mode", dependencies=[Depends(get_admin_key)])
async def switch_chat_mode(request: Request):
    """
    供真人客服手動切換指定用戶的聊天模式
    請求 Body 應為 JSON: { "user_id": "Uxxxx", "mode": "human" }
    """
    data = await request.json()
    user_id = data.get("user_id")
    mode = data.get("mode")

    if not user_id or mode not in ["ai", "human"]:
        raise HTTPException(status_code=400, detail="Invalid request body.")
    
    try:
        update_data = {"user_id": user_id, "mode": mode}
        if mode == 'human':
            # 更新為真人模式時，記錄當下時間
            update_data["last_human_reply_at"] = datetime.now(timezone.utc).isoformat()
        
        # 使用 upsert，無論用戶是否存在都能新增或更新
        await supabase.table("chat_states").upsert(update_data).execute()
        
        logger.info(f"已將使用者 {user_id} 的模式切換為 {mode}")
        return {"status": "success", "user_id": user_id, "new_mode": mode}
    except Exception as e:
        logger.error(f"切換模式失敗: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/revert_to_ai", dependencies=[Depends(get_admin_key)])
async def revert_inactive_chats_to_ai():
    """
    供 Vercel Cron Job 定時呼叫，將超時的真人對話自動交還給 AI
    """
    # 設定超時時間，例如 2 分鐘
    timeout_minutes = 2
    revert_time_threshold = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
    
    try:
        # 尋找所有處於 'human' 模式，且最後真人回覆時間早於閾值的對話
        response = await supabase.table("chat_states").select("user_id").eq("mode", "human").lt("last_human_reply_at", revert_time_threshold.isoformat()).execute()
        
        users_to_revert = [item['user_id'] for item in response.data]
        
        if not users_to_revert:
            return {"status": "success", "reverted_count": 0, "message": "沒有需要交還的對話。"}

        # 將這些超時的對話模式更新回 'ai'
        await supabase.table("chat_states").update({"mode": "ai"}).in_("user_id", users_to_revert).execute()
        
        logger.info(f"已將 {len(users_to_revert)} 個超時對話交還給 AI: {users_to_revert}")
        return {"status": "success", "reverted_count": len(users_to_revert), "reverted_users": users_to_revert}
        
    except Exception as e:
        logger.error(f"自動交還 AI 失敗: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Webhook 路由 (維持不變) ---
@app.post("/api/webhook")
async def line_webhook(request: Request, background_tasks: BackgroundTasks):
    # ... (與前一版相同) ...

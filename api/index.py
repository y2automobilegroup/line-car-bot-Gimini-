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

# --- Environment Variables & Initialization ---
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "your-strong-secret-key")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
parser = WebhookParser(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

supabase: AsyncClient = create_async_client(SUPABASE_URL, SUPABASE_KEY)

def get_admin_key(request: Request):
    api_key = request.headers.get("X-Admin-API-Key")
    if api_key != ADMIN_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid Admin API Key")
    return api_key

# --- Core Functions (with fix) ---

async def get_chat_mode(user_id: str) -> str:
    """
    (FIXED) Safely gets the chat mode for a given user from Supabase.
    Handles cases where the user does not exist yet.
    """
    try:
        # Don't use .single() here as it will error on new users
        response = await supabase.table("chat_states").select("mode").eq("user_id", user_id).execute()
        
        # Check if any data was returned
        if response.data:
            return response.data[0].get("mode", "ai")
        else:
            # User not found, default to 'ai' mode
            return "ai"
    except Exception as e:
        logger.error(f"Error getting chat mode for user {user_id}: {e}")
        # In case of any other error, default to 'ai' to be safe
        return "ai"

# (The rest of the file remains the same)
# ... handle_user_query, format_car_details, etc. ...
async def handle_user_query(user_question: str) -> str:
    try:
        processed_question = convert_chinese_numerals_in_text(user_question)
        logger.info(f"Original question: '{user_question}', Processed question: '{processed_question}'")
        
        query_term = f"%{processed_question}%"
        response = await supabase.table("cars").select("*").or_(
            f"brand.ilike.{query_term}",
            f"model.ilike.{query_term}",
            f"color.ilike.{query_term}",
            f"description.ilike.{query_term}",
            f"title.ilike.{query_term}"
        ).limit(5).execute()

        cars_data = response.data
        if not cars_data: return "I'm sorry, based on your description, I couldn't find any matching vehicles in the database. You could try different keywords, like 'blue Toyota' or '2023 SUV'."

        formatted_cars = "\n\n---\n\n".join([format_car_details(car) for car in cars_data])
        system_prompt = "You are a professional, friendly, and helpful car sales consultant. Your task is to answer customer questions based on the vehicle database information provided by the company. Please reply in Traditional Chinese. Your answers should be based on the 'Matching Vehicle Data' provided below. Do not invent information that is not in the data. If the information is incomplete, you can kindly remind the customer that they are welcome to visit the store for more details. When replying, first summarize which models might meet the customer's needs, and then you can introduce one or two of them in a bit more detail."
        user_prompt = f"The customer's question is: '{user_question}'\n\nBelow is the potentially matching vehicle data found in our database:\n---\n{formatted_cars}\n---\n\nPlease answer the customer's question in the tone of a professional sales consultant based on the above information."

        chat_completion = await openai_client.chat.completions.create(
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            model="gpt-3.5-turbo",
            temperature=0.7,
        )
        return chat_completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error handling query: {e}")
        return "The system encountered a problem, please try again later."
        
def format_car_details(car: dict) -> str:
    details = [
        f"Brand/Model: {car.get('brand', 'N/A')} / {car.get('model', 'N/A')}",
        f"Year/Month: {car.get('year', 'N/A')}/{car.get('month', 'N/A')}",
        f"Price: {car.get('price', 'N/A')} million",
        f"Color: {car.get('color', 'N/A')}",
        f"Displacement: {car.get('displacement', 'N/A')} c.c.",
        f"Transmission: {car.get('transmission', 'N/A')}",
        f"Fuel: {car.get('fuel', 'N/A')}",
        f"Vehicle Title: {car.get('title', 'N/A')}",
        f"Vehicle Description: {car.get('description', 'N/A')}",
    ]
    return "\n".join(detail for detail in details if 'N/A' not in detail)

def convert_chinese_numerals_in_text(text: str) -> str:
    pattern = r'[零一二兩三四五六七八九十百千萬]+'
    def replacer(match):
        num_str = match.group(0)
        arabic_num = chinese_to_arabic(num_str)
        return str(arabic_num) if arabic_num > 0 else num_str
    return re.sub(pattern, replacer, text)
    
def chinese_to_arabic(cn_num_str: str) -> int:
    if not cn_num_str: return 0
    total, section, number = 0, 0, 0
    for char in cn_num_str:
        if char in CHINESE_NUM_MAP: number = CHINESE_NUM_MAP[char]
        elif char in CHINESE_UNIT_MAP:
            unit = CHINESE_UNIT_MAP[char]
            if unit == 10000:
                section += number; total += section * unit; section = 0
            else:
                section += (number if number > 0 else 1) * unit
            number = 0
    total += section + number
    return total

CHINESE_NUM_MAP = {
    "零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9
}
CHINESE_UNIT_MAP = { "十": 10, "百": 100, "千": 1000, "萬": 10000 }


async def process_text_message(event: MessageEvent):
    user_id = event.source.user_id
    user_text = event.message.text
    reply_token = event.reply_token

    current_mode = await get_chat_mode(user_id)
    
    if current_mode == 'human':
        logger.info(f"User {user_id} is in 'human' mode, AI is ignoring the message.")
        return 

    logger.info(f"User {user_id} is in 'ai' mode, processing message: {user_text}")
    
    reply_text = await handle_user_query(user_text)
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=reply_text)])
        )
    logger.info(f"Replied to user {user_id}")

@app.post("/admin/switch_mode", dependencies=[Depends(get_admin_key)])
async def switch_chat_mode(request: Request):
    data = await request.json()
    user_id = data.get("user_id")
    mode = data.get("mode")

    if not user_id or mode not in ["ai", "human"]:
        raise HTTPException(status_code=400, detail="Invalid request body.")
    
    try:
        update_data = {"user_id": user_id, "mode": mode}
        if mode == 'human':
            update_data["last_human_reply_at"] = datetime.now(timezone.utc).isoformat()
        
        await supabase.table("chat_states").upsert(update_data).execute()
        
        logger.info(f"Switched mode for user {user_id} to {mode}")
        return {"status": "success", "user_id": user_id, "new_mode": mode}
    except Exception as e:
        logger.error(f"Failed to switch mode: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/admin/revert_to_ai", dependencies=[Depends(get_admin_key)])
async def revert_inactive_chats_to_ai():
    timeout_minutes = 2
    revert_time_threshold = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
    
    try:
        response = await supabase.table("chat_states").select("user_id").eq("mode", "human").lt("last_human_reply_at", revert_time_threshold.isoformat()).execute()
        
        users_to_revert = [item['user_id'] for item in response.data]
        
        if not users_to_revert:
            return {"status": "success", "reverted_count": 0, "message": "No conversations to revert."}

        await supabase.table("chat_states").update({"mode": "ai"}).in_("user_id", users_to_revert).execute()
        
        logger.info(f"Reverted {len(users_to_revert)} timed-out conversations to AI: {users_to_revert}")
        return {"status": "success", "reverted_count": len(users_to_revert), "reverted_users": users_to_revert}
        
    except Exception as e:
        logger.error(f"Failed to automatically revert to AI: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/webhook")
async def line_webhook(request: Request, background_tasks: BackgroundTasks):
    signature = request.headers.get("X-Line-Signature")
    if not signature:
        raise HTTPException(status_code=400, detail="X-Line-Signature header is missing")
    
    body = await request.body()
    
    try:
        events = parser.parse(body.decode(), signature)
    except InvalidSignatureError:
        logger.warning("Invalid signature. Please check your channel secret.")
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            background_tasks.add_task(process_text_message, event)

    return "OK"

@app.get("/")
async def root():
    return {"message": "LINE Bot is running."}

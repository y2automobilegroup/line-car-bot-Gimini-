import os
import logging
import re
import asyncio # 引入 asyncio
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)
# (修正) 引入更多 Webhook 事件類型
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from supabase import create_async_client, AsyncClient
from openai import AsyncOpenAI

# --- 環境變數設定 (不變) ---
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# --- 初始化 (修正：WebhookHandler 改為 WebhookParser) ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
# (修正) 使用 Parser 而不是 Handler，以便我們手動控制事件處理
parser = WebhookParser(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

def get_supabase_client() -> AsyncClient:
    return create_async_client(SUPABASE_URL, SUPABASE_KEY)

# --- 中文數字轉換功能 (不變) ---
CHINESE_NUM_MAP = {
    "零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9
}
CHINESE_UNIT_MAP = { "十": 10, "百": 100, "千": 1000, "萬": 10000 }

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

def convert_chinese_numerals_in_text(text: str) -> str:
    pattern = r'[零一二兩三四五六七八九十百千萬]+'
    def replacer(match):
        num_str = match.group(0)
        arabic_num = chinese_to_arabic(num_str)
        return str(arabic_num) if arabic_num > 0 else num_str
    return re.sub(pattern, replacer, text)

# --- 核心功能 (不變) ---
def format_car_details(car: dict) -> str:
    details = [
        f"廠牌/車種: {car.get('廠牌', 'N/A')} / {car.get('車種', 'N/A')}",
        f"年份/月份: {car.get('年', 'N/A')}年 {car.get('月', 'N/A')}月",
        f"價格: {car.get('車價', 'N/A')} 萬元",
        f"顏色: {car.get('顏色', 'N/A')}",
        f"排氣量: {car.get('排氣量', 'N/A')} c.c.",
        f"排檔: {car.get('排檔', 'N/A')}",
        f"燃料: {car.get('燃料', 'N/A')}",
        f"車輛標題: {car.get('車輛標題', 'N/A')}",
        f"車輛介紹: {car.get('車輛介紹', 'N/A')}",
    ]
    return "\n".join(detail for detail in details if 'N/A' not in detail)

async def handle_user_query(user_question: str) -> str:
    try:
        processed_question = convert_chinese_numerals_in_text(user_question)
        logger.info(f"原始問題: '{user_question}', 處理後問題: '{processed_question}'")
        
        supabase = get_supabase_client()
        query_term = f"%{processed_question}%"
        response = await supabase.table("cars").select("*").or_(
            f"廠牌.ilike.{query_term}",
            f"車種.ilike.{query_term}",
            f"顏色.ilike.{query_term}",
            f"車輛介紹.ilike.{query_term}",
            f"車輛標題.ilike.{query_term}"
        ).limit(5).execute()

        cars_data = response.data
        if not cars_data: return "不好意思，根據您的描述，目前資料庫中找不到符合的車輛。您可以試著更換關鍵字，例如「藍色 豐田」或「2023年的休旅車」。"

        formatted_cars = "\n\n---\n\n".join([format_car_details(car) for car in cars_data])
        system_prompt = "你是一位專業、親切且樂於助人的汽車銷售顧問。你的任務是根據公司提供的車輛資料庫資訊，來回答客戶的問題。請用繁體中文回答。你的回答應該基於以下提供的「符合的車輛資料」。請不要編造資料中沒有的資訊。如果資料不完整，可以友善地提醒客戶歡迎來店詳談。回答時，先總結有哪些車款可能符合客戶需求，然後可以稍微詳細介紹其中一兩款。"
        user_prompt = f"客戶的問題是：「{user_question}」\n\n以下是從我們資料庫中找到，可能符合的車輛資料：\n---\n{formatted_cars}\n---\n\n請根據以上資料，以專業銷售顧問的口吻回答客戶的問題。"

        chat_completion = await openai_client.chat.completions.create(
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            model="gpt-3.5-turbo",
            temperature=0.7,
        )
        return chat_completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"處理查詢時發生錯誤: {e}")
        return "系統發生了一點問題，請稍後再試。"

# (新增) 建立一個專門在背景處理訊息的函式
async def process_text_message(event: MessageEvent):
    user_text = event.message.text
    reply_token = event.reply_token
    
    logger.info(f"開始在背景處理使用者 {event.source.user_id} 的訊息: {user_text}")
    
    # 執行耗時的任務
    reply_text = await handle_user_query(user_text)
    
    # 任務完成後，發送回覆訊息
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=reply_text)])
        )
    logger.info(f"已完成背景任務並回覆使用者 {event.source.user_id}")


# --- LINE Webhook 路由 (重大修正) ---

@app.post("/api/webhook")
async def line_webhook(request: Request, background_tasks: BackgroundTasks):
    # 驗證 LINE 簽名
    signature = request.headers.get("X-Line-Signature")
    if not signature:
        raise HTTPException(status_code=400, detail="X-Line-Signature header is missing")
    
    body = await request.body()
    
    try:
        # 解析 Webhook 事件
        events = parser.parse(body.decode(), signature)
    except InvalidSignatureError:
        logger.warning("Invalid signature. Please check your channel secret.")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # 遍歷所有事件
    for event in events:
        # 如果是文字訊息事件，就將它加入背景任務
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            background_tasks.add_task(process_text_message, event)

    # **關鍵**：立即回傳 "OK" 給 LINE 平台，不等背景任務完成
    return "OK"

# (移除) 不再需要舊的 handle_text_message 和 task 函式

@app.get("/")
async def root():
    return {"message": "LINE Bot is running."}

# LINE x GPT x Supabase 汽車查詢機器人

這是一個整合 LINE Messaging API、OpenAI GPT-3.5 以及 Supabase 資料庫的 Python 專案。使用者可以透過 LINE@ 官方帳號詢問車輛相關問題，機器人會查詢 Supabase 中的 `cars` 表格，並利用 GPT 生成自然的回覆。

本專案使用 FastAPI 框架建置，並設計為可直接部署於 [Vercel](https://vercel.com/)。

## 專案特色

- **LINE Webhook**: 使用 `fastapi` 接收 LINE 平台傳送的事件。
- **中文數字轉換**: 自動將使用者問題中的 "五十萬" 轉換為 "500000"，提升查詢準確率。
- **資料庫查詢**: 連接至 [Supabase](https://supabase.com/)，並對 `cars` 表格進行即時查詢。
- **AI 生成回覆**: 將查詢到的車輛資料與使用者問題傳送給 [OpenAI GPT](https://openai.com/)，生成專業且人性化的回覆。
- **Serverless 部署**: 專為 Vercel 設計，實現快速、彈性的部署。

## 設定步驟

請參考專案內的教學或開發者說明來完成環境變數與 Webhook 的設定。
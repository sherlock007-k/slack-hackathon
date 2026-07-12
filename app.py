import os
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_bolt.middleware.assistant import Assistant
from openai import OpenAI

load_dotenv()

app = App(token=os.environ.get("SLACK_BOT_TOKEN"))
assistant = Assistant()
openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# -----------------------------------------------------------------------------
# CORE LOGIC: Channel History Fetching & Summarization
# -----------------------------------------------------------------------------

def fetch_channel_history(channel_id):
    """Fetches the last 50 messages from a specific channel."""
    try:
        result = app.client.conversations_history(channel=channel_id, limit=50)
        messages = result.get("messages", [])
        
        if not messages:
            return "No messages found in this channel."

        formatted_history = []
        for msg in reversed(messages): 
            user = msg.get("user", "Unknown User")
            text = msg.get("text", "")
            if not msg.get("bot_id"):
                formatted_history.append(f"[{user}]: {text}")
                
        return "\n".join(formatted_history)
    except Exception as e:
        return f"🚨 Error fetching channel history: {e}"

def generate_digest(chat_history):
    """Sends raw chat logs to OpenAI and requests a scannable dashboard summary."""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are StackSage's core summarization engine. Analyze the provided Slack chat history "
                        "and generate a concise, professional summary dashboard. Format your output using standard markdown. "
                        "Include three sections:\n"
                        "1. 📋 *Key Decisions Made* (Bullet points of finalized items)\n"
                        "2. ⚠️ *Open Questions/Discussions* (Items still being debated)\n"
                        "3. ⚡ *Action Items* (Clear tasks assigned to users or teams)"
                    )
                },
                {"role": "user", "content": f"Summarize this chat history:\n\n{chat_history}"}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"🚨 Error generating digest: {e}"

def answer_channel_question(chat_history, question):
    """Feeds channel history to the LLM to answer a specific question with user tag formatting."""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are StackSage, a Slack workspace assistant. You will be provided with a raw chat history. "
                        "Answer the user's question based STRICTLY on the information in that chat history. "
                        "If the answer is not in the history, say 'I cannot find the answer in the recent channel history.' "
                        "Always cite the user who provided the information. "
                        "CRITICAL: If a user is represented by an alphanumeric ID (e.g., U0BGGU619L3), you MUST wrap it in angle brackets and an @ symbol like this: <@U0BGGU619L3>. Do not use square brackets. Slack will automatically translate this into a mention."
                    )
                },
                {"role": "user", "content": f"Chat History:\n{chat_history}\n\nQuestion: {question}"}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"🚨 Error generating answer: {e}"

# -----------------------------------------------------------------------------
# SLACK INTERACTION HANDLERS
# -----------------------------------------------------------------------------

def process_user_intent(user_text, say):
    """Helper to parse commands and coordinate the features."""
    cleaned_text = user_text.strip().lower()
    parts = user_text.split()

    # -----------------------------------------
    # FEATURE 1: Channel Digest
    # -----------------------------------------
    if cleaned_text.startswith("digest") or cleaned_text.startswith("summarize"):
        if len(parts) < 2:
            say(text="Please provide a channel ID. Example: `digest C01234567`")
            return
            
        target_channel = parts[1].strip("<>#")
        say(text=f"🔍 Compiling digest for channel *{target_channel}*...")
        
        raw_history = fetch_channel_history(target_channel)
        if "Error" in raw_history or "No messages found" in raw_history:
            say(text=raw_history)
            return
            
        summary_text = generate_digest(raw_history)
        
        say(
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"✨ *StackSage Channel Digest for <#{target_channel}>* ✨"}
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": summary_text}
                },
                {"type": "divider"},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✋ Claim Action Items"},
                            "style": "primary",
                            "action_id": "claim_tasks_action"
                        }
                    ]
                }
            ],
            text="Your channel digest is ready!" 
        )

    # -----------------------------------------
    # FEATURE 2: Ask the Channel
    # -----------------------------------------
    elif cleaned_text.startswith("ask"):
        if len(parts) < 3:
            say(text="Please provide a channel ID and a question. Example: `ask C01234567 What is the project deadline?`")
            return
        
        target_channel = parts[1].strip("<>#")
        question = " ".join(parts[2:]) 
        
        say(text=f"🧠 Scanning history in *{target_channel}* for your answer...")
        
        raw_history = fetch_channel_history(target_channel)
        if "Error" in raw_history or "No messages found" in raw_history:
            say(text=raw_history)
            return
            
        answer_text = answer_channel_question(raw_history, question)
        say(
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": f"❓ *Question:* {question}"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"💡 *Answer:* {answer_text}"}}
            ],
            text="I found an answer to your question!"
        )

    # -----------------------------------------
    # FEATURE 3: Standup Draft
    # -----------------------------------------
    elif cleaned_text.startswith("standup"):
        if len(parts) < 3:
            say(text="Please provide a channel ID and your update. Example: `standup C01234567 Yesterday I coded the DB, today I am testing, no blockers.`")
            return
        
        target_channel = parts[1].strip("<>#")
        raw_update = " ".join(parts[2:])
        
        say(text="✍️ Formatting your update into a professional standup...")
        
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You format messy text into a clean daily standup. Use three bold headings: *Yesterday*, *Today*, and *Blockers*. Keep it concise and professional. Use bullet points."
                    },
                    {"role": "user", "content": raw_update}
                ]
            )
            formatted_standup = response.choices[0].message.content
            
            say(
                blocks=[
                    {"type": "section", "text": {"type": "mrkdwn", "text": "👀 *Preview of your Standup:*"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": formatted_standup}},
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "🚀 Post to Channel"},
                                "style": "primary",
                                "action_id": "post_standup_action",
                                "value": f"{target_channel}|{formatted_standup}" 
                            }
                        ]
                    }
                ],
                text="Your standup preview is ready!"
            )
        except Exception as e:
            say(text=f"🚨 Error generating standup: {e}")

    # -----------------------------------------
    # FALLBACK
    # -----------------------------------------
    else:
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are StackSage. Remind the user of your commands: `digest [channel_id]` or `ask [channel_id] [question]`."},
                    {"role": "user", "content": user_text}
                ]
            )
            say(text=response.choices[0].message.content)
        except Exception as e:
            say(text=f"🚨 Error: {e}")

# -----------------------------------------------------------------------------
# INTERACTIVITY LISTENERS (BUTTON CLICKS)
# -----------------------------------------------------------------------------

@app.action("post_standup_action")
def handle_post_standup(ack, body, client, logger):
    """Fires when the user clicks 'Post to Channel' on their standup draft."""
    ack() 
    
    action_value = body["actions"][0]["value"]
    target_channel, standup_text = action_value.split("|", 1)
    user_id = body["user"]["id"]

    try:
        client.chat_postMessage(
            channel=target_channel,
            text=f"🗓️ *Daily Standup from <@{user_id}>:*\n\n{standup_text}"
        )
        
        client.chat_update(
            channel=body["container"]["channel_id"],
            ts=body["container"]["message_ts"],
            text="✅ Standup posted successfully!",
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": "✅ *Your standup was successfully posted!*"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": standup_text}}
            ]
        )
    except Exception as e:
        logger.error(f"Error posting standup: {e}")

@app.action("claim_tasks_action")
def handle_claim_tasks(ack, body, client, logger):
    """Fires when a user clicks 'Claim Action Items' on a digest."""
    ack() 
    
    user_id = body["user"]["id"]
    
    try:
        # Extract the original AI summary text safely to avoid invalid_blocks metadata error
        original_summary = body["message"]["blocks"][2]["text"]["text"]
        
        # Build completely fresh blocks
        new_blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "✨ *StackSage Channel Digest* ✨"}
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": original_summary}
            },
            {"type": "divider"},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn", 
                        "text": f"✅ *Action items successfully claimed by <@{user_id}>*"
                    }
                ]
            }
        ]

        # Update the original message dynamically
        client.chat_update(
            channel=body["container"]["channel_id"],
            ts=body["container"]["message_ts"],
            text="Action items claimed!",
            blocks=new_blocks
        )
    except Exception as e:
        logger.error(f"Error updating digest message: {e}")

@assistant.user_message
def handle_user_message(payload, say):
    process_user_intent(payload.get("text", ""), say)

@app.event("message")
def handle_standard_message(payload, say):
    if payload.get("subtype"):
        return
    process_user_intent(payload.get("text", ""), say)

app.use(assistant)

if __name__ == "__main__":
    print("StackSage running")
    SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN")).start()
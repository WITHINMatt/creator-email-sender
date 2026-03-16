import os
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import anthropic
import gspread
from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_SHEET_URL = os.environ["GOOGLE_SHEET_URL"]
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json")
SENDER_NAME = os.environ["SENDER_NAME"]
CLIENT_NAME = os.environ["CLIENT_NAME"]

EMAIL_SUBJECT = f"Collaboration Opportunity with {CLIENT_NAME}"

SYSTEM_PROMPT = f"""You write short, friendly outreach emails to content creators on behalf of {SENDER_NAME} from {CLIENT_NAME}.

Rules:
- Keep it to 3-4 sentences MAX. Be brief and human.
- Open with a personalized line that references the creator by name.
- Briefly pitch the collaboration opportunity with {CLIENT_NAME}.
- End with a soft CTA (e.g. "Would love to chat if you're open to it").
- Do NOT use subject lines, greetings like "Dear", or sign-offs like "Best regards". Just the body text.
- Sound like a real person, not a marketing email. Casual but professional."""


def get_creators_from_sheet():
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_JSON)
    sheet = gc.open_by_url(GOOGLE_SHEET_URL).sheet1
    rows = sheet.get_all_records()

    creators = []
    for row in rows:
        name = row.get("name", "").strip()
        email = row.get("email", "").strip()
        if name and email:
            creators.append({"name": name, "email": email})
    return creators


def generate_email_body(creator_name):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Write a brief outreach email to a creator named {creator_name}.",
            }
        ],
    )
    return message.content[0].text


def send_email(to_address, creator_name, body):
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{SENDER_NAME} <{GMAIL_ADDRESS}>"
    msg["To"] = to_address
    msg["Subject"] = EMAIL_SUBJECT

    full_body = f"{body}\n\n{SENDER_NAME}\n{CLIENT_NAME}"
    msg.attach(MIMEText(full_body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, to_address, msg.as_string())


def main():
    print(f"Fetching creators from Google Sheet...")
    creators = get_creators_from_sheet()
    print(f"Found {len(creators)} creators.\n")

    if not creators:
        print("No creators found. Check your sheet has 'name' and 'email' columns.")
        return

    # Preview mode — show first email before sending all
    print("--- PREVIEW (first creator) ---")
    first = creators[0]
    preview_body = generate_email_body(first["name"])
    print(f"To: {first['name']} <{first['email']}>")
    print(f"Subject: {EMAIL_SUBJECT}")
    print(f"\n{preview_body}\n")
    print(f"{SENDER_NAME}")
    print(f"{CLIENT_NAME}")
    print("-------------------------------\n")

    confirm = input(f"Send to all {len(creators)} creators? (y/n): ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    sent = 0
    failed = 0
    for i, creator in enumerate(creators):
        try:
            print(f"[{i+1}/{len(creators)}] Generating email for {creator['name']}...")
            # Reuse preview body for first creator
            body = preview_body if i == 0 else generate_email_body(creator["name"])

            print(f"  Sending to {creator['email']}...")
            send_email(creator["email"], creator["name"], body)
            sent += 1
            print(f"  Sent!")

            # Small delay to avoid Gmail rate limits
            if i < len(creators) - 1:
                time.sleep(2)

        except Exception as e:
            failed += 1
            print(f"  FAILED: {e}")

    print(f"\nDone! Sent: {sent}, Failed: {failed}")


if __name__ == "__main__":
    main()

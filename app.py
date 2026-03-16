import os
import smtplib
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import anthropic
import gspread
from dotenv import load_dotenv, set_key

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ENV_PATH)


def env(key, fallback=""):
    return os.environ.get(key, fallback)


# ---------------------------------------------------------------------------
# Core logic (same as send_emails.py but decoupled from globals)
# ---------------------------------------------------------------------------

def build_system_prompt(sender_name, client_name):
    return f"""You write short, friendly outreach emails to content creators on behalf of {sender_name} from {client_name}.

Rules:
- Keep it to 3-4 sentences MAX. Be brief and human.
- Open with a personalized line that references the creator by name.
- Briefly pitch the collaboration opportunity with {client_name}.
- End with a soft CTA (e.g. "Would love to chat if you're open to it").
- Do NOT use subject lines, greetings like "Dear", or sign-offs like "Best regards". Just the body text.
- Sound like a real person, not a marketing email. Casual but professional."""


def fetch_creators(sheet_url, service_account_json):
    gc = gspread.service_account(filename=service_account_json)
    sheet = gc.open_by_url(sheet_url).sheet1
    rows = sheet.get_all_records()
    creators = []
    for row in rows:
        name = str(row.get("name", "")).strip()
        email = str(row.get("email", "")).strip()
        if name and email:
            creators.append({"name": name, "email": email})
    return creators


def generate_email_body(api_key, system_prompt, creator_name):
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        system=system_prompt,
        messages=[
            {"role": "user", "content": f"Write a brief outreach email to a creator named {creator_name}."}
        ],
    )
    return message.content[0].text


def send_one_email(gmail_addr, gmail_pw, sender_name, client_name, subject, to_addr, body):
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{sender_name} <{gmail_addr}>"
    msg["To"] = to_addr
    msg["Subject"] = subject
    full_body = f"{body}\n\n{sender_name}\n{client_name}"
    msg.attach(MIMEText(full_body, "plain"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_addr, gmail_pw)
        server.sendmail(gmail_addr, to_addr, msg.as_string())


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Creator Email Outreach")
        self.geometry("780x720")
        self.resizable(True, True)

        self.creators = []
        self.generated_emails = {}  # index -> body text

        self._build_settings_frame()
        self._build_sheet_frame()
        self._build_preview_frame()
        self._build_actions_frame()
        self._build_log_frame()

    # ---- Settings ----
    def _build_settings_frame(self):
        frame = ttk.LabelFrame(self, text="Settings", padding=8)
        frame.pack(fill="x", padx=10, pady=(10, 4))

        fields = [
            ("Gmail Address", "gmail_addr", env("GMAIL_ADDRESS")),
            ("Gmail App Password", "gmail_pw", env("GMAIL_APP_PASSWORD")),
            ("Anthropic API Key", "api_key", env("ANTHROPIC_API_KEY")),
            ("Google Sheet URL", "sheet_url", env("GOOGLE_SHEET_URL")),
            ("Service Account JSON", "sa_json", env("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json")),
            ("Your Name", "sender_name", env("SENDER_NAME")),
            ("Client / Brand", "client_name", env("CLIENT_NAME")),
        ]

        self.settings = {}
        for i, (label, key, default) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=default)
            show = "*" if "password" in key.lower() or "api_key" in key else ""
            entry = ttk.Entry(frame, textvariable=var, width=60, show=show)
            entry.grid(row=i, column=1, sticky="ew", padx=(8, 0), pady=2)
            self.settings[key] = var

        frame.columnconfigure(1, weight=1)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=len(fields), column=0, columnspan=2, pady=(6, 0))
        ttk.Button(btn_frame, text="Save to .env", command=self._save_env).pack(side="left")

    def _save_env(self):
        mapping = {
            "GMAIL_ADDRESS": "gmail_addr",
            "GMAIL_APP_PASSWORD": "gmail_pw",
            "ANTHROPIC_API_KEY": "api_key",
            "GOOGLE_SHEET_URL": "sheet_url",
            "GOOGLE_SERVICE_ACCOUNT_JSON": "sa_json",
            "SENDER_NAME": "sender_name",
            "CLIENT_NAME": "client_name",
        }
        # Create .env if it doesn't exist
        if not os.path.exists(ENV_PATH):
            open(ENV_PATH, "w").close()
        for env_key, setting_key in mapping.items():
            set_key(ENV_PATH, env_key, self.settings[setting_key].get())
        self._log("Settings saved to .env")

    # ---- Sheet / creators list ----
    def _build_sheet_frame(self):
        frame = ttk.LabelFrame(self, text="Creators", padding=8)
        frame.pack(fill="x", padx=10, pady=4)

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="Fetch from Sheet", command=self._fetch_creators).pack(side="left")
        self.creator_count_label = ttk.Label(btn_row, text="No creators loaded")
        self.creator_count_label.pack(side="left", padx=12)

        cols = ("name", "email", "status")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=6)
        self.tree.heading("name", text="Name")
        self.tree.heading("email", text="Email")
        self.tree.heading("status", text="Status")
        self.tree.column("name", width=180)
        self.tree.column("email", width=260)
        self.tree.column("status", width=100)
        self.tree.pack(fill="x", pady=(6, 0))

    def _fetch_creators(self):
        self._log("Fetching creators from Google Sheet...")
        self.tree.delete(*self.tree.get_children())
        self.generated_emails.clear()

        def _work():
            try:
                self.creators = fetch_creators(
                    self.settings["sheet_url"].get(),
                    self.settings["sa_json"].get(),
                )
                self.after(0, self._populate_tree)
            except Exception as e:
                self.after(0, lambda: self._log(f"Error fetching sheet: {e}"))

        threading.Thread(target=_work, daemon=True).start()

    def _populate_tree(self):
        for c in self.creators:
            self.tree.insert("", "end", values=(c["name"], c["email"], "pending"))
        self.creator_count_label.config(text=f"{len(self.creators)} creators loaded")
        self._log(f"Loaded {len(self.creators)} creators.")

    # ---- Preview ----
    def _build_preview_frame(self):
        frame = ttk.LabelFrame(self, text="Email Preview", padding=8)
        frame.pack(fill="both", expand=True, padx=10, pady=4)

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="Generate Preview for Selected", command=self._preview_selected).pack(side="left")
        ttk.Button(btn_row, text="Generate All Emails", command=self._generate_all).pack(side="left", padx=8)

        self.subject_var = tk.StringVar(value="Collaboration Opportunity with {client}")
        subj_row = ttk.Frame(frame)
        subj_row.pack(fill="x", pady=(6, 0))
        ttk.Label(subj_row, text="Subject:").pack(side="left")
        ttk.Entry(subj_row, textvariable=self.subject_var, width=60).pack(side="left", padx=(8, 0), fill="x", expand=True)

        self.preview_text = scrolledtext.ScrolledText(frame, height=8, wrap="word")
        self.preview_text.pack(fill="both", expand=True, pady=(6, 0))

    def _get_subject(self):
        return self.subject_var.get().replace("{client}", self.settings["client_name"].get())

    def _preview_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select a creator", "Click a row in the creators table first.")
            return
        item = self.tree.item(sel[0])
        name = item["values"][0]
        self._log(f"Generating preview for {name}...")

        idx = self.tree.index(sel[0])

        def _work():
            try:
                prompt = build_system_prompt(self.settings["sender_name"].get(), self.settings["client_name"].get())
                body = generate_email_body(self.settings["api_key"].get(), prompt, name)
                self.generated_emails[idx] = body
                self.after(0, lambda: self._show_preview(name, body))
            except Exception as e:
                self.after(0, lambda: self._log(f"Error generating preview: {e}"))

        threading.Thread(target=_work, daemon=True).start()

    def _show_preview(self, name, body):
        sender = self.settings["sender_name"].get()
        client = self.settings["client_name"].get()
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("end", f"To: {name}\n")
        self.preview_text.insert("end", f"Subject: {self._get_subject()}\n\n")
        self.preview_text.insert("end", f"{body}\n\n{sender}\n{client}")
        self._log(f"Preview ready for {name}.")

    def _generate_all(self):
        if not self.creators:
            messagebox.showinfo("No creators", "Fetch creators from the sheet first.")
            return
        self._log("Generating all emails...")

        def _work():
            prompt = build_system_prompt(self.settings["sender_name"].get(), self.settings["client_name"].get())
            for i, c in enumerate(self.creators):
                if i in self.generated_emails:
                    continue
                try:
                    body = generate_email_body(self.settings["api_key"].get(), prompt, c["name"])
                    self.generated_emails[i] = body
                    self.after(0, lambda n=c["name"]: self._log(f"  Generated for {n}"))
                except Exception as e:
                    self.after(0, lambda n=c["name"], err=e: self._log(f"  Failed for {n}: {err}"))
            self.after(0, lambda: self._log(f"All emails generated ({len(self.generated_emails)}/{len(self.creators)})."))

        threading.Thread(target=_work, daemon=True).start()

    # ---- Actions ----
    def _build_actions_frame(self):
        frame = ttk.Frame(self, padding=8)
        frame.pack(fill="x", padx=10)
        self.send_btn = ttk.Button(frame, text="Send All Emails", command=self._send_all)
        self.send_btn.pack(side="left")
        self.progress = ttk.Progressbar(frame, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 0))

    def _send_all(self):
        if not self.creators:
            messagebox.showinfo("No creators", "Fetch creators first.")
            return
        if not self.generated_emails:
            messagebox.showinfo("No emails generated", "Generate emails before sending.")
            return

        if not messagebox.askyesno("Confirm", f"Send {len(self.generated_emails)} emails?"):
            return

        self.send_btn.config(state="disabled")
        self.progress["maximum"] = len(self.creators)
        self.progress["value"] = 0

        def _work():
            sent = 0
            failed = 0
            for i, c in enumerate(self.creators):
                body = self.generated_emails.get(i)
                if not body:
                    self.after(0, lambda n=c["name"]: self._set_row_status(n, "skipped"))
                    continue
                try:
                    send_one_email(
                        self.settings["gmail_addr"].get(),
                        self.settings["gmail_pw"].get(),
                        self.settings["sender_name"].get(),
                        self.settings["client_name"].get(),
                        self._get_subject(),
                        c["email"],
                        body,
                    )
                    sent += 1
                    self.after(0, lambda n=c["name"]: self._set_row_status(n, "sent"))
                    self.after(0, lambda v=i + 1: self.progress.configure(value=v))
                except Exception as e:
                    failed += 1
                    self.after(0, lambda n=c["name"], err=e: (
                        self._set_row_status(n, "FAILED"),
                        self._log(f"  Failed {n}: {err}"),
                    ))

                import time
                time.sleep(2)

            self.after(0, lambda: self._log(f"Done! Sent: {sent}, Failed: {failed}"))
            self.after(0, lambda: self.send_btn.config(state="normal"))

        threading.Thread(target=_work, daemon=True).start()

    def _set_row_status(self, name, status):
        for item_id in self.tree.get_children():
            if self.tree.item(item_id)["values"][0] == name:
                vals = list(self.tree.item(item_id)["values"])
                vals[2] = status
                self.tree.item(item_id, values=vals)
                break

    # ---- Log ----
    def _build_log_frame(self):
        frame = ttk.LabelFrame(self, text="Log", padding=4)
        frame.pack(fill="x", padx=10, pady=(4, 10))
        self.log_text = scrolledtext.ScrolledText(frame, height=5, wrap="word", state="disabled")
        self.log_text.pack(fill="x")

    def _log(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")


if __name__ == "__main__":
    App().mainloop()

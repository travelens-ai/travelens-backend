import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from core.config import SMTP_EMAIL, SMTP_PASSWORD


def generate_otp():
    return str(random.randint(100000, 999999))


def send_otp_email(email, otp, purpose="signup"):
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print(f"SMTP not configured. OTP for {email}: {otp}")
        return True

    subject = "Travelens - Email Verification OTP" if purpose == "signup" else "Travelens - Password Reset OTP"
    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; padding: 20px;">
        <h2 style="color: #2c3e50;">Travelens</h2>
        <p>Your OTP for {'email verification' if purpose == 'signup' else 'password reset'} is:</p>
        <h1 style="color: #3498db; letter-spacing: 5px; font-size: 36px;">{otp}</h1>
        <p>This OTP is valid for <strong>10 minutes</strong>.</p>
        <p style="color: #7f8c8d;">If you didn't request this, please ignore this email.</p>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_EMAIL
    msg["To"] = email
    msg.attach(MIMEText(body, "html"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Failed to send OTP email: {e}")
        return False

import os
import qrcode
import firebase_admin
from firebase_admin import credentials, db
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from config import *

# ---------------- FIREBASE SETUP ----------------
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred, {'databaseURL': DB_URL})

def get_user(uid):
    return db.reference(f"users/{uid}").get()

def update_user(uid, data):
    db.reference(f"users/{uid}").update(data)

# ---------------- BOT ----------------
app = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# States
user_state = {}          # uid -> "add_amount" / "qr_sent" / "awaiting_ss" / "wd"
pending_payments = {}    # uid -> {"amount": amt, "message_id": msg_id, "channel_id": PAYMENT_CHANNEL}
admin_state = {}         # admin_id -> {"action": "accept_amount", "user_id": uid}

# ---------------- START WITH JOIN VERIFICATION ----------------
@app.on_message(filters.command("start"))
async def start(client, message):
    uid = message.from_user.id
    args = message.text.split()

    # Create user if not exists
    if not get_user(uid):
        update_user(uid, {
            "bot_balance": 0,
            "web_balance": 0,
            "referrals": 0,
            "banned": False
        })

        # Referral logic
        if len(args) > 1 and args[1].startswith("ref_"):
            ref_id = int(args[1].split("_")[1])
            if ref_id != uid:
                ref_user = get_user(ref_id)
                if ref_user:
                    db.reference(f"users/{ref_id}/bot_balance").set(ref_user["bot_balance"] + 0.25)
                    db.reference(f"users/{ref_id}/referrals").set(ref_user["referrals"] + 1)
                    await app.send_message(ref_id, "🎉 New referral! +₹0.25 added to your balance.")
                    await message.reply(f"✅ You were referred by user {ref_id}")

    # Join buttons
    join_buttons = [
        [InlineKeyboardButton("📢 Join Channel 1", url=f"https://t.me/{CHANNELS[0][1:]}")],
        [InlineKeyboardButton("📢 Join Channel 2", url=f"https://t.me/{CHANNELS[1][1:]}")],
        [InlineKeyboardButton("📢 Join Channel 3", url=f"https://t.me/{CHANNELS[2][1:]}")],
        [InlineKeyboardButton("🔒 Private Group", url=PRIVATE_CHANNEL)],
        [InlineKeyboardButton("✅ Verify & Start", callback_data="verify")]
    ]
    await message.reply("⚠️ Please join all channels first.", reply_markup=InlineKeyboardMarkup(join_buttons))

# ---------------- VERIFICATION ----------------
@app.on_callback_query(filters.regex("verify"))
async def verify(client, cb):
    uid = cb.from_user.id
    for ch in CHANNELS:
        try:
            member = await app.get_chat_member(ch, uid)
            if member.status == "left":
                return await cb.answer("❌ You must join all channels!", show_alert=True)
        except:
            return await cb.answer("⚠️ Could not verify. Try again later.", show_alert=True)

    await cb.message.edit("✅ Verified! Welcome to the bot.", reply_markup=main_menu())

# ---------------- MAIN MENU (2 buttons per row) ----------------
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 SMM Services", web_app=WebAppInfo(url=WEB_URL)),
         InlineKeyboardButton("📦 Order Status", callback_data="order")],
        [InlineKeyboardButton("♻️ Add Fund", callback_data="add"),
         InlineKeyboardButton("💸 Withdraw", callback_data="wd")],
        [InlineKeyboardButton("💰 Refer & Earn", callback_data="ref"),
         InlineKeyboardButton("🪙 Earn Money", callback_data="earn")],
        [InlineKeyboardButton("💳 My Balance", callback_data="bal"),
         InlineKeyboardButton("📖 How to Use", callback_data="how")]
    ])

# ---------------- CALLBACK HANDLERS (Menu + Payment flows) ----------------
@app.on_callback_query(filters.regex("add"))
async def add_fund_start(client, cb):
    user_state[cb.from_user.id] = "add_amount"
    await cb.message.reply("💳 Enter the amount you want to add:")

@app.on_callback_query(filters.regex("ref"))
async def referral(client, cb):
    bot = await app.get_me()
    link = f"https://t.me/{bot.username}?start=ref_{cb.from_user.id}"
    await cb.message.reply(f"🔗 Your referral link:\n{link}\n\nEarn ₹0.25 per referral!")

@app.on_callback_query(filters.regex("earn"))
async def earn_info(client, cb):
    await cb.message.reply("💰 Earn ₹500-700/day using our SMM methods. (Details here...)")

@app.on_callback_query(filters.regex("bal"))
async def balance(client, cb):
    user = get_user(cb.from_user.id)
    text = f"🤖 **Bot Balance:** ₹{user['bot_balance']}\n🌐 **Web Balance:** ₹{user['web_balance']}"
    await cb.message.reply(text)

@app.on_callback_query(filters.regex("wd"))
async def withdraw_start(client, cb):
    user_state[cb.from_user.id] = "wd"
    await cb.message.reply("💸 Enter the amount you want to withdraw (min ₹2):")

@app.on_callback_query(filters.regex("order"))
async def order_status(client, cb):
    await cb.message.reply("📦 Order status will be available soon via the web app.")

@app.on_callback_query(filters.regex("how"))
async def how_to_use(client, cb):
    text = (
        "📖 **How to Use**\n\n"
        "1. Add funds using UPI.\n"
        "2. Buy SMM services from the Web App.\n"
        "3. Check order status & balance here.\n"
        "4. Withdraw when balance reaches ₹2."
    )
    await cb.message.reply(text)

# ---------------- PAYMENT FLOW INLINE BUTTONS (Done/Cancel) ----------------
@app.on_callback_query(filters.regex(r"pay_done_(\d+)"))
async def pay_done(client, cb):
    uid = int(cb.matches[0].group(1))
    if uid != cb.from_user.id:
        return await cb.answer("⛔ This is not for you.", show_alert=True)

    user_state[uid] = "awaiting_ss"
    await cb.message.delete()  # remove QR message
    await app.send_message(uid, "📸 Please send a screenshot of your successful payment.")

@app.on_callback_query(filters.regex(r"pay_cancel_(\d+)"))
async def pay_cancel(client, cb):
    uid = int(cb.matches[0].group(1))
    if uid != cb.from_user.id:
        return await cb.answer("⛔ This is not for you.", show_alert=True)

    user_state.pop(uid, None)
    await cb.message.delete()
    await app.send_message(uid, "❌ Payment cancelled.")

# ---------------- SCREENSHOT HANDLER (after Done) ----------------
@app.on_message(filters.photo)
async def handle_screenshot(client, msg):
    uid = msg.from_user.id
    if user_state.get(uid) != "awaiting_ss":
        return  # ignore random photos

    # Retrieve stored amount from pending_payments? We'll store amount when QR generated.
    # Let's find pending data
    payment = pending_payments.pop(uid, None)
    if not payment:
        await msg.reply("⚠️ Session expired. Please start add fund again.")
        user_state.pop(uid, None)
        return

    amount = payment["amount"]

    # Forward screenshot to payment channel with accept/reject buttons
    caption = (
        f"💰 **New Payment Request**\n"
        f"👤 User ID: {uid}\n"
        f"💵 Amount: ₹{amount}\n\n"
        f"Please verify and click:"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Accept", callback_data=f"accept_{uid}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"reject_{uid}")]
    ])

    sent_msg = await app.send_photo(
        PAYMENT_CHANNEL,
        msg.photo.file_id,
        caption=caption,
        reply_markup=buttons
    )

    # Save reference to the sent message for later editing (optional)
    # We'll use callback data user_id to find message when admin responds,
    # but we need the message ID to edit. We'll store it in pending_payments again or separate dict.
    pending_payments[uid] = {"amount": amount, "channel_msg_id": sent_msg.id}

    # Inform user
    await msg.reply("⏳ Your payment is being verified. We'll update you shortly.")
    user_state.pop(uid, None)  # clear state, user now waits

# ---------------- ADMIN APPROVAL/REJECTION CALLBACKS ----------------
@app.on_callback_query(filters.regex(r"accept_(\d+)"))
async def admin_accept(client, cb):
    if cb.from_user.id not in ADMIN_IDS:
        return await cb.answer("⛔ Unauthorized", show_alert=True)

    uid = int(cb.matches[0].group(1))
    payment = pending_payments.get(uid)
    if not payment:
        return await cb.answer("⚠️ Request expired or already handled.", show_alert=True)

    # Ask admin for the exact amount paid
    admin_state[cb.from_user.id] = {"action": "accept_amount", "user_id": uid}
    await cb.answer("Please enter the amount the user paid.", show_alert=True)
    await app.send_message(cb.from_user.id, f"✍️ Enter the exact amount paid by user {uid}:")

@app.on_callback_query(filters.regex(r"reject_(\d+)"))
async def admin_reject(client, cb):
    if cb.from_user.id not in ADMIN_IDS:
        return await cb.answer("⛔ Unauthorized", show_alert=True)

    uid = int(cb.matches[0].group(1))
    payment = pending_payments.pop(uid, None)
    if not payment:
        return await cb.answer("⚠️ Request already handled.", show_alert=True)

    # Edit channel message to show rejected
    try:
        await app.edit_message_caption(
            PAYMENT_CHANNEL,
            payment["channel_msg_id"],
            caption=cb.message.caption.markdown + "\n\n❌ **Rejected**"
        )
    except:
        pass

    await app.send_message(uid, "❌ Your payment has been rejected. Please try again.")
    await cb.message.edit_reply_markup()  # remove buttons
    await cb.answer("Rejected!")

# ---------------- ADMIN ENTERS AMOUNT AFTER ACCEPT ----------------
@app.on_message(filters.text & filters.user(ADMIN_IDS))
async def admin_amount_input(client, msg):
    admin_id = msg.from_user.id
    if admin_id not in admin_state or admin_state[admin_id]["action"] != "accept_amount":
        return  # ignore if not in that flow

    uid = admin_state[admin_id]["user_id"]
    payment = pending_payments.get(uid)
    if not payment:
        del admin_state[admin_id]
        return await msg.reply("⚠️ Payment request no longer exists.")

    try:
        amt = float(msg.text)
    except:
        return await msg.reply("❌ Invalid amount. Please enter a number.")

    # Update user's web balance (and optionally bot_balance)
    user = get_user(uid)
    new_web_balance = user["web_balance"] + amt
    update_user(uid, {"web_balance": new_web_balance})

    # Edit channel message to show accepted
    try:
        await app.edit_message_caption(
            PAYMENT_CHANNEL,
            payment["channel_msg_id"],
            caption=msg.reply_to_message.caption.markdown + f"\n\n✅ **Accepted by admin {admin_id}**\n💵 Amount: ₹{amt}"
        )
    except:
        pass

    # Notify user
    await app.send_message(uid, f"✅ Your payment of ₹{amt} has been approved!\nFunds added to your Web Balance.")

    # Cleanup
    pending_payments.pop(uid, None)
    admin_state.pop(admin_id, None)
    await msg.reply("✅ Done.")

# ---------------- WITHDRAW AMOUNT INPUT ----------------
@app.on_message(filters.text)
async def text_handler(client, msg):
    uid = msg.from_user.id
    state = user_state.get(uid)

    # Add fund: entering amount
    if state == "add_amount":
        try:
            amt = float(msg.text)
        except:
            return await msg.reply("❌ Please enter a valid number.")

        # Generate QR code
        upi_string = f"upi://pay?pa={UPI_ID}&pn={UPI_NAME}&am={amt}&cu=INR"
        qr = qrcode.make(upi_string)
        file_path = f"qr_{uid}.png"
        qr.save(file_path)

        # Store amount temporarily for later screenshot
        pending_payments[uid] = {"amount": amt, "channel_msg_id": None}

        # Buttons: Done / Cancel (2 per row as requested)
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Done", callback_data=f"pay_done_{uid}"),
             InlineKeyboardButton("❌ Cancel", callback_data=f"pay_cancel_{uid}")]
        ])

        await msg.reply_photo(
            photo=file_path,
            caption=f"💳 Scan the QR to pay ₹{amt}.\nThen press **Done** and upload screenshot.",
            reply_markup=buttons
        )
        os.remove(file_path)
        user_state[uid] = "qr_sent"

    # Withdraw: entering amount
    elif state == "wd":
        try:
            amt = float(msg.text)
        except:
            return await msg.reply("❌ Invalid amount.")

        user = get_user(uid)
        if amt < 2:
            return await msg.reply("❌ Minimum withdrawal is ₹2.")
        if user["bot_balance"] < amt:
            return await msg.reply("❌ Insufficient bot balance.")

        # Send withdrawal request to admins (same channel)
        await app.send_message(
            PAYMENT_CHANNEL,
            f"🚨 **Withdrawal Request**\n👤 User ID: {uid}\n💸 Amount: ₹{amt}"
        )
        await msg.reply("✅ Withdrawal request sent. You will be notified when processed.")
        user_state.pop(uid, None)

    # If user sends text while waiting for screenshot
    elif state == "awaiting_ss":
        await msg.reply("📸 Please send a **photo** (screenshot), not text.")

    else:
        # ignore other random texts
        pass

# ---------------- ADMIN DIRECT FUND ADD (old method) ----------------
@app.on_message(filters.command("addfunds") & filters.user(ADMIN_IDS))
async def add_funds_direct(client, msg):
    try:
        uid = int(msg.command[1])
        amt = float(msg.command[2])
    except:
        return await msg.reply("Usage: /addfunds <user_id> <amount>")

    user = get_user(uid)
    if not user:
        return await msg.reply("User not found.")
    update_user(uid, {"web_balance": user["web_balance"] + amt})
    await app.send_message(uid, f"✅ ₹{amt} added to your balance by admin.")
    await msg.reply("✅ Done.")

# ---------------- RUN ----------------
app.run()

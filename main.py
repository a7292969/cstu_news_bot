"""CSTU News Bot"""

import threading
import json
import logging
from os import environ
from threading import Thread
from time import sleep
from telegram import ParseMode, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackContext, \
    MessageHandler, Filters, ConversationHandler, CallbackQueryHandler, DispatcherHandlerStop
from telegram.error import ChatMigrated, BadRequest

log = logging.getLogger(__name__)
lock = threading.Lock()

SETTINGS_FILENAME = 'settings.json'

# 'addstaff' states
STATE_CONTACT = 1

# news send states
STATE_MESSAGE = 1


class Global:
    """Global constants"""

    settings_updated = False
    settings = {}

    def __init__(self):
        with open(SETTINGS_FILENAME, 'ta+', encoding='utf-8') as file:
            file.seek(0)
            settings_str = file.read().strip()

        if len(settings_str) == 0:
            self.settings = {'groups': [], 'staff': []}
            self.settings_updated = True
        else:
            self.settings = json.loads(settings_str)


g = Global()


def on_start(update: Update, _: CallbackContext):
    # update.message.reply_text('Hi! I broadcast news. Just send me a message you want to ')
    pass


def on_new_chat_member(update: Update, _: CallbackContext):
    """When the bot is added to some group, this event is emitted.
    This event is also emitted when a new user is added to the group with this bot.
    Subscribes the group to the news"""

    with lock:
        g.settings['groups'].append(update.effective_chat.id)
        g.settings_updated = True


def on_left_chat_member(update: Update, _: CallbackContext):
    """When the bot is removed from some group, this event is emitted.
    This event is also emitted when a user left the group with this bot.
    Unsubscribes the group from the news."""

    with lock:
        g.settings['groups'].remove(update.effective_chat.id)
        g.settings_updated = True


def add_staff(update: Update, _: CallbackContext):
    """Starts a conversation of adding a staff member"""

    if update.effective_user.id not in g.settings['staff']:
        update.message.reply_text('You are not authorized to add staff')
        raise DispatcherHandlerStop()  # prevent from running handlers from other groups

    update.message.reply_text('Send me a staff member contact')
    return STATE_CONTACT


def make_groups_markup(ctx: CallbackContext, selected_groups: set[int]):
    """Creates inline buttons for controlling news destination"""

    buttons = list()

    for group_id in g.settings['groups']:
        checkmark = '✅' if group_id in selected_groups else '☐ '
        group = ctx.bot.get_chat(group_id)
        buttons.append([InlineKeyboardButton(
            f'{checkmark} {group.title}', callback_data=group_id)])

    buttons.append([InlineKeyboardButton('Cancel', callback_data='cancel'),
                    InlineKeyboardButton(
                        'Send to all', callback_data='send_to_all')])

    if len(selected_groups) > 0:
        buttons[-1].append(InlineKeyboardButton('Send', callback_data='send'))

    return InlineKeyboardMarkup(buttons)


def make_send_confirm_markup():
    """Creates inline buttons for confirming news broadcast"""

    return InlineKeyboardMarkup(
        [[InlineKeyboardButton('Back', callback_data='confirm-back'),
         InlineKeyboardButton('Confirm', callback_data='confirm-confirm')
          ]])


CONFIRM_SENDING_TEXT = 'Do you confirm sending news?'
CHOOSE_GROUPS_TO_SEND_TO_TEXT = 'Send more messages or choose groups to broadcast news to:'


def on_message(update: Update, ctx: CallbackContext):
    """Handles news broadcasting"""

    if update.effective_user.id not in g.settings['staff']:
        update.message.reply_text('You are not authorized to broadcast news')
        return
    if len(g.settings['groups']) == 0:
        update.message.reply_text('No groups to broadcast news to!')
        return

    if 'reply_msg_id' in ctx.user_data and ctx.user_data['reply_msg_id']:
        ctx.bot.delete_message(update.effective_chat.id,
                               ctx.user_data['reply_msg_id'])
    if 'messages' not in ctx.user_data:
        ctx.user_data['messages'] = []
    if 'selected_groups' not in ctx.user_data:
        ctx.user_data['selected_groups'] = set()

    ctx.user_data['messages'].append(update.message.message_id)

    reply_msg = update.message.reply_text(
        CHOOSE_GROUPS_TO_SEND_TO_TEXT,
        reply_markup=make_groups_markup(ctx, ctx.user_data['selected_groups']))

    ctx.user_data['reply_msg_id'] = reply_msg.message_id


def query_callback(update: Update, ctx: CallbackContext):
    """Group selection handler"""

    messages = ctx.user_data['messages']
    selected_groups = ctx.user_data['selected_groups']
    groups_to_send_to = []

    if update.callback_query.data == 'cancel':
        update.callback_query.message.edit_text('News broadcast cancelled')
    elif update.callback_query.data == 'send_to_all':
        ctx.bot.edit_message_text(CONFIRM_SENDING_TEXT, update.effective_chat.id,
                                  ctx.user_data['reply_msg_id'])
        update.callback_query.message.edit_reply_markup(
            make_send_confirm_markup())
        ctx.user_data['send_to_all'] = True
        return
    elif update.callback_query.data == 'send':
        ctx.bot.edit_message_text(CONFIRM_SENDING_TEXT, update.effective_chat.id,
                                  ctx.user_data['reply_msg_id'])
        update.callback_query.message.edit_reply_markup(
            make_send_confirm_markup())
        ctx.user_data['send_to_all'] = False
        return
    elif update.callback_query.data == 'confirm-back':
        ctx.bot.edit_message_text(CHOOSE_GROUPS_TO_SEND_TO_TEXT, update.effective_chat.id,
                                  ctx.user_data['reply_msg_id'])
        update.callback_query.message.edit_reply_markup(
            make_groups_markup(ctx, selected_groups))
        return
    elif update.callback_query.data == 'confirm-confirm':
        if ctx.user_data['send_to_all']:
            groups_to_send_to = g.settings['groups']
        else:
            groups_to_send_to = selected_groups
    else:
        group_id = int(update.callback_query.data)

        if group_id in selected_groups:
            selected_groups.remove(group_id)
        else:
            selected_groups.add(group_id)

        update.callback_query.message.edit_reply_markup(
            make_groups_markup(ctx, selected_groups))
        return

    for group_id in groups_to_send_to:
        curr_group_id = group_id

        for msg_id in messages:
            success = True

            while True:
                # pylint: disable=bare-except
                try:
                    ctx.bot.copy_message(
                        curr_group_id, update.effective_chat.id, msg_id)
                except ChatMigrated as err:
                    with lock:
                        g.settings['groups'].remove(curr_group_id)
                        g.settings['groups'].append(err.new_chat_id)
                        g.settings_updated = True
                    curr_group_id = err.new_chat_id
                    # Continue to try again with the new group id
                    continue
                except BadRequest:
                    group = ctx.bot.get_chat(group_id)
                    update.effective_chat.send_message(
                        f'Failed to send news to *{group.title}*.'
                        ' Maybe they blocked sending news from the bot.',
                        parse_mode=ParseMode.MARKDOWN)
                    success = False
                    break
                except Exception as err:
                    raise err
                break

            # If no success with current group then continue to the next group
            if not success:
                break

    if len(groups_to_send_to) > 0:
        update.callback_query.message.edit_text(
            'News have been successfully boardcasted!')

    messages.clear()
    selected_groups.clear()
    ctx.user_data['reply_msg_id'] = None


def cancel_add_staff(update: Update, _: CallbackContext):
    """Cancelling staff addition"""

    update.message.reply_text('Cancelled')
    return ConversationHandler.END


def add_staff__contact(update: Update, _: CallbackContext):
    """Handles staff member contact submission"""

    if not update.message.contact:
        update.message.reply_text("Please send a staff member contact")
        return None

    with lock:
        g.settings['staff'].append(update.message.contact.user_id)
        g.settings_updated = True

    update.message.reply_text("Staff member successfully added!")
    return ConversationHandler.END


def on_error(update: Update, ctx: CallbackContext):
    """Error handler"""
    log.exception(ctx.error)
    update.message.reply_text("Server Error happened")


def settings_saver():
    """Checks for settings updates and saves them"""
    while True:
        sleep(1)

        if not g.settings_updated:
            continue

        with lock:
            with open(SETTINGS_FILENAME, 'tw', encoding='utf-8') as file:
                file.write(json.dumps(g.settings))
            g.settings_updated = False


updater = Updater(environ.get('TELEGRAM_BOT_TOKEN'))
updater.dispatcher.add_error_handler(on_error)

updater.dispatcher.add_handler(CommandHandler('start', on_start))

updater.dispatcher.add_handler(MessageHandler(
    Filters.status_update.new_chat_members | Filters.status_update.chat_created,
    on_new_chat_member))

updater.dispatcher.add_handler(MessageHandler(
    Filters.status_update.left_chat_member, on_left_chat_member))

updater.dispatcher.add_handler(ConversationHandler(
    entry_points=[CommandHandler('addstaff', add_staff)],
    states={
        STATE_CONTACT: [MessageHandler(
            Filters.all & ~Filters.command, add_staff__contact)]
    },
    fallbacks=[CommandHandler('cancel', cancel_add_staff)]
))

updater.dispatcher.add_handler(MessageHandler(
    (Filters.text | Filters.photo) & ~Filters.command, on_message))

updater.dispatcher.add_handler(CallbackQueryHandler(query_callback))


Thread(target=settings_saver).start()

# TODO PROD: change from polling to webhook
updater.start_polling()
updater.idle()

import os
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# =========================
# SETTINGS
# =========================

STARTING_CHIPS = 1000
SMALL_BLIND = 10
BIG_BLIND = 20

TOKEN = os.getenv("BOT_TOKEN")


# =========================
# CARDS
# =========================

RANKS = "23456789TJQKA"
SUITS = ["♠", "♥", "♦", "♣"]


def create_deck():
    return [
        rank + suit
        for suit in SUITS
        for rank in RANKS
    ]


# =========================
# PLAYER
# =========================

@dataclass
class Player:
    user_id: int
    name: str

    chips: int = STARTING_CHIPS

    cards: List[str] = field(
        default_factory=list
    )

    folded: bool = False

    bet: int = 0

    all_in: bool = False


# =========================
# TABLE
# =========================

@dataclass
class Table:

    players: Dict[int, Player] = field(
        default_factory=dict
    )

    deck: List[str] = field(
        default_factory=list
    )

    board: List[str] = field(
        default_factory=list
    )

    pot: int = 0

    current_bet: int = 0

    turn: Optional[int] = None

    stage: str = "waiting"

    dealer: int = 0


tables: Dict[int, Table] = {}


# =========================
# HELPERS
# =========================

def get_table(chat_id: int):

    if chat_id not in tables:
        tables[chat_id] = Table()

    return tables[chat_id]


def card_string(cards):

    if not cards:
        return "—"

    return " ".join(cards)


def active_players(table):

    return [
        p
        for p in table.players.values()
        if not p.folded
    ]


def next_player(table):

    players = active_players(table)

    if not players:
        table.turn = None
        return

    ids = [p.user_id for p in players]

    if table.turn not in ids:

        table.turn = ids[0]

        return

    index = ids.index(table.turn)

    for i in range(1, len(ids) + 1):

        nxt = ids[
            (index + i) % len(ids)
        ]

        player = table.players[nxt]

        if not player.folded and not player.all_in:

            table.turn = nxt

            return

    table.turn = None


# =========================
# HAND RANKING
# =========================

def card_value(card):

    rank = card[0]

    return RANKS.index(rank) + 2


def evaluate_hand(cards):

    values = sorted(
        [card_value(c) for c in cards],
        reverse=True
    )

    suits = [c[1] for c in cards]

    counts = {}

    for v in values:

        counts[v] = counts.get(v, 0) + 1

    unique = sorted(
        set(values),
        reverse=True
    )

    # Straight

    straight_high = None

    if 14 in unique:

        unique.append(1)

    for i in range(len(unique) - 4):

        part = unique[i:i + 5]

        if part[0] - part[4] == 4:

            straight_high = part[0]

            break

    # Flush

    flush = None

    for suit in SUITS:

        suited = sorted(
            [
                card_value(c)
                for c in cards
                if c[1] == suit
            ],
            reverse=True
        )

        if len(suited) >= 5:

            flush = suited[:5]

            break

    # Straight Flush

    if flush:

        fu = sorted(
            set(flush),
            reverse=True
        )

        if 14 in fu:

            fu.append(1)

        for i in range(len(fu) - 4):

            part = fu[i:i + 5]

            if part[0] - part[4] == 4:

                return (
                    8,
                    part[0]
                )

    # Four of a kind

    fours = [
        v for v, c in counts.items()
        if c == 4
    ]

    if fours:

        four = max(fours)

        kicker = max(
            v for v in values
            if v != four
        )

        return (
            7,
            four,
            kicker
        )

    # Full house

    trips = sorted(
        [
            v for v, c in counts.items()
            if c >= 3
        ],
        reverse=True
    )

    pairs = sorted(
        [
            v for v, c in counts.items()
            if c >= 2
        ],
        reverse=True
    )

    if trips:

        trip = trips[0]

        remaining_pairs = [
            v for v in pairs
            if v != trip
        ]

        if remaining_pairs:

            return (
                6,
                trip,
                remaining_pairs[0]
            )

    # Flush

    if flush:

        return (
            5,
            *flush
        )

    # Straight

    if straight_high:

        return (
            4,
            straight_high
        )

    # Three of a kind

    if trips:

        trip = trips[0]

        kickers = [
            v for v in values
            if v != trip
        ][:2]

        return (
            3,
            trip,
            *kickers
        )

    # Two pair

    pair_values = sorted(
        [
            v for v, c in counts.items()
            if c >= 2
        ],
        reverse=True
    )

    if len(pair_values) >= 2:

        p1 = pair_values[0]
        p2 = pair_values[1]

        kicker = max(
            v for v in values
            if v != p1 and v != p2
        )

        return (
            2,
            p1,
            p2,
            kicker
        )

    # One pair

    if len(pair_values) == 1:

        pair = pair_values[0]

        kickers = [
            v for v in values
            if v != pair
        ][:3]

        return (
            1,
            pair,
            *kickers
        )

    # High card

    return (
        0,
        *values[:5]
    )


def best_hand(cards):

    from itertools import combinations

    best = None

    for combo in combinations(cards, 5):

        score = evaluate_hand(combo)

        if best is None or score > best:

            best = score

    return best


# =========================
# UI
# =========================

def game_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "✅ Check",
                callback_data="check"
            ),

            InlineKeyboardButton(
                "💰 Call",
                callback_data="call"
            )
        ],

        [
            InlineKeyboardButton(
                "📈 Raise 20",
                callback_data="raise"
            ),

            InlineKeyboardButton(
                "🔥 All-in",
                callback_data="allin"
            )
        ],

        [
            InlineKeyboardButton(
                "❌ Fold",
                callback_data="fold"
            )
        ]

    ])


def table_text(table):

    text = (
        "🃏 *TEXAS HOLD'EM*\n\n"
    )

    text += (
        f"💰 Pot: *{table.pot}*\n"
    )

    text += (
        f"🂠 Board: "
        f"{card_string(table.board)}\n\n"
    )

    text += "👥 Players:\n"

    for p in table.players.values():

        status = "🎮"

        if p.folded:

            status = "❌"

        elif p.all_in:

            status = "🔥"

        if p.user_id == table.turn:

            status += " ⏳"

        text += (
            f"{status} {p.name} — "
            f"{p.chips} chips\n"
        )

    return text


# =========================
# COMMANDS
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "🃏 *Poker Bot*\n\n"

        "Texas Hold'em با چیپ مجازی.\n\n"

        "/join — ورود به میز\n"
        "/leave — خروج\n"
        "/table — وضعیت میز\n"
        "/startgame — شروع بازی\n"
        "/help — راهنما",

        parse_mode="Markdown"
    )


async def join(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_id = update.effective_chat.id

    user = update.effective_user

    table = get_table(chat_id)

    if table.stage != "waiting":

        await update.message.reply_text(
            "⛔ بازی در حال اجراست."
        )

        return

    if user.id in table.players:

        await update.message.reply_text(
            "شما قبلاً وارد میز شده‌اید."
        )

        return

    table.players[user.id] = Player(

        user_id=user.id,

        name=user.full_name

    )

    await update.message.reply_text(

        f"🎮 {user.full_name} وارد میز شد!\n\n"
        f"💰 چیپ: {STARTING_CHIPS}"

    )


async def leave(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_id = update.effective_chat.id

    user = update.effective_user

    table = get_table(chat_id)

    if table.stage != "waiting":

        await update.message.reply_text(
            "⛔ هنگام بازی نمی‌توانید خارج شوید."
        )

        return

    if user.id not in table.players:

        await update.message.reply_text(
            "شما در میز نیستید."
        )

        return

    del table.players[user.id]

    await update.message.reply_text(
        "👋 خارج شدید."
    )


async def table_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    table = get_table(
        update.effective_chat.id
    )

    if not table.players:

        await update.message.reply_text(
            "میز خالی است.\n\n/join"
        )

        return

    await update.message.reply_text(

        table_text(table),

        parse_mode="Markdown",

        reply_markup=(
            game_keyboard()
            if table.stage != "waiting"
            else None
        )
    )


# =========================
# START GAME
# =========================

async def start_game(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_id = update.effective_chat.id

    table = get_table(chat_id)

    if len(table.players) < 2:

        await update.message.reply_text(
            "❗ حداقل ۲ بازیکن لازم است."
        )

        return

    if table.stage != "waiting":

        await update.message.reply_text(
            "⛔ یک دست در حال اجراست."
        )

        return

    table.deck = create_deck()

    random.shuffle(table.deck)

    table.board = []

    table.pot = 0

    table.current_bet = BIG_BLIND

    table.stage = "preflop"

    players = list(
        table.players.values()
    )

    # Reset players

    for player in players:

        player.cards = []

        player.folded = False

        player.bet = 0

        player.all_in = False

    # Deal cards

    for _ in range(2):

        for player in players:

            player.cards.append(
                table.deck.pop()
            )

    # Dealer

    table.dealer = (
        table.dealer + 1
    ) % len(players)

    # Blinds

    sb_index = (
        table.dealer + 1
    ) % len(players)

    bb_index = (
        table.dealer + 2
    ) % len(players)

    sb = players[sb_index]

    bb = players[bb_index]

    sb_amount = min(
        SMALL_BLIND,
        sb.chips
    )

    bb_amount = min(
        BIG_BLIND,
        bb.chips
    )

    sb.chips -= sb_amount

    sb.bet += sb_amount

    table.pot += sb_amount

    bb.chips -= bb_amount

    bb.bet += bb_amount

    table.pot += bb_amount

    if bb.chips == 0:

        bb.all_in = True

    table.turn = players[
        (bb_index + 1) % len(players)
    ].user_id

    await update.message.reply_text(

        table_text(table) +

        "\n\n"
        "🎲 دست شروع شد!\n"
        "🎴 کارت‌های شما در پیام خصوصی ارسال می‌شوند.",

        parse_mode="Markdown",

        reply_markup=game_keyboard()
    )

    # Private cards

    for player in players:

        try:

            await context.bot.send_message(

                chat_id=player.user_id,

                text=(
                    "🃏 *کارت‌های شما:*\n\n"
                    f"{card_string(player.cards)}"
                ),

                parse_mode="Markdown"
            )

        except Exception:

            pass


# =========================
# BETTING
# =========================

def put_chips(player, amount):

    amount = min(
        amount,
        player.chips
    )

    player.chips -= amount

    player.bet += amount

    return amount


def everybody_matched(table):

    players = [
        p for p in table.players.values()
        if not p.folded and not p.all_in
    ]

    return all(
        p.bet == table.current_bet
        for p in players
    )


def deal_board(table):

    if table.stage == "preflop":

        table.board.extend(
            [
                table.deck.pop(),
                table.deck.pop(),
                table.deck.pop()
            ]
        )

        table.stage = "flop"

    elif table.stage == "flop":

        table.board.append(
            table.deck.pop()
        )

        table.stage = "turn"

    elif table.stage == "turn":

        table.board.append(
            table.deck.pop()
        )

        table.stage = "river"


def reset_bets(table):

    for p in table.players.values():

        p.bet = 0

    table.current_bet = 0


# =========================
# FINISH HAND
# =========================

async def finish_hand(
    query,
    table
):

    active = [
        p
        for p in table.players.values()
        if not p.folded
    ]

    if len(active) == 1:

        winner = active[0]

        winner.chips += table.pot

        result = (
            f"🏆 *{winner.name}* برنده شد!\n\n"
            f"💰 جایزه: {table.pot} چیپ"
        )

    else:

        results = []

        for player in active:

            score = best_hand(
                player.cards +
                table.board
            )

            results.append(
                (score, player)
            )

        results.sort(
            key=lambda x: x[0],
            reverse=True
        )

        best_score = results[0][0]

        winners = [
            p
            for score, p in results
            if score == best_score
        ]

        prize = table.pot // len(winners)

        for winner in winners:

            winner.chips += prize

        names = ", ".join(
            w.name for w in winners
        )

        result = (
            f"🏆 برنده: *{names}*\n\n"
            f"💰 جایزه هر نفر: {prize} چیپ"
        )

    table.stage = "waiting"

    table.turn = None

    table.pot = 0

    table.board = []

    await query.edit_message_text(

        table_text(table) +

        "\n\n" +

        result,

        parse_mode="Markdown"
    )


# =========================
# BUTTONS
# =========================

async def button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    chat_id = query.message.chat_id

    user_id = query.from_user.id

    table = get_table(chat_id)

    if table.stage == "waiting":

        await query.answer(
            "بازی فعالی نیست.",
            show_alert=True
        )

        return

    if table.turn != user_id:

        await query.answer(
            "⏳ هنوز نوبت شما نیست.",
            show_alert=True
        )

        return

    player = table.players[user_id]

    action = query.data

    # Fold

    if action == "fold":

        player.folded = True

    # Check

    elif action == "check":

        if player.bet != table.current_bet:

            await query.answer(
                "❌ نمی‌توانید Check کنید.",
                show_alert=True
            )

            return

    # Call

    elif action == "call":

        needed = (
            table.current_bet -
            player.bet
        )

        amount = put_chips(
            player,
            needed
        )

        table.pot += amount

        if player.chips == 0:

            player.all_in = True

    # Raise

    elif action == "raise":

        target = (
            table.current_bet +
            BIG_BLIND
        )

        needed = target - player.bet

        if player.chips < needed:

            await query.answer(
                "چیپ کافی نیست.",
                show_alert=True
            )

            return

        amount = put_chips(
            player,
            needed
        )

        table.pot += amount

        table.current_bet = target

    # All-in

    elif action == "allin":

        amount = player.chips

        player.chips = 0

        player.bet += amount

        table.pot += amount

        player.all_in = True

        if player.bet > table.current_bet:

            table.current_bet = player.bet

    # Check winner

    active = active_players(table)

    if len(active) <= 1:

        await finish_hand(
            query,
            table
        )

        return

    # Next player

    next_player(table)

    # New round

    if table.turn is None:

        if table.stage == "river":

            await finish_hand(
                query,
                table
            )

            return

        deal_board(table)

        reset_bets(table)

        players = [
            p
            for p in table.players.values()
            if not p.folded
            and not p.all_in
        ]

        if players:

            table.turn = players[0].user_id

        else:

            await finish_hand(
                query,
                table
            )

            return

    await query.edit_message_text(

        table_text(table),

        parse_mode="Markdown",

        reply_markup=game_keyboard()
    )


# =========================
# HELP
# =========================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "📖 *راهنما*\n\n"

        "/join — ورود به میز\n"
        "/leave — خروج\n"
        "/table — وضعیت میز\n"
        "/startgame — شروع دست\n\n"

        "🃏 Texas Hold'em\n"
        "💰 چیپ‌ها مجازی هستند.",

        parse_mode="Markdown"
    )


# =========================
# MAIN
# =========================

def main():

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN تنظیم نشده است."
        )

    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "join",
            join
        )
    )

    app.add_handler(
        CommandHandler(
            "leave",
            leave
        )
    )

    app.add_handler(
        CommandHandler(
            "table",
            table_command
        )
    )

    app.add_handler(
        CommandHandler(
            "startgame",
            start_game
        )
    )

    app.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            button
        )
    )

    print(
        "Poker bot is running..."
    )

    app.run_polling()


if __name__ == "__main__":

    main()

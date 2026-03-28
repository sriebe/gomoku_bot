import asyncio
import websockets
import json
import random
import sys
import os
import time

# Load configuration from config.json
def load_config():
    """Load configuration from config.json"""
    config_file = "config.json"
    if os.path.exists(config_file):
        with open(config_file, 'r') as f:
            return json.load(f)
    return {
        "server_url": "ws://localhost:8000/ws",
        "username": "",
        "token": ""
    }

# Load config
config = load_config()
SERVER_URL = config.get("server_url", "ws://localhost:8000/ws")
TOKEN = config.get("token", "")
USERNAME = config.get("username", "")

# Board representation
BOARD_SIZE = 19
EMPTY = 0
BLACK = 1
WHITE = 2

# Search / evaluation
DIRS = ((0, 1), (1, 0), (1, 1), (1, -1))
INF = 10**9
# Stay under a 5s server turn limit
MOVE_TIME_BUDGET_SEC = 4.9
MAX_CANDIDATES = 18
MAX_DEPTH_CAP = 8
NODE_CHECK_INTERVAL = 2048

# Pattern weights for full-board segment evaluation (both sides)
W_FIVE = 200_000
W_OPEN_FOUR = 45_000
W_CLOSED_FOUR = 6_000
W_OPEN_THREE = 900
W_CLOSED_THREE = 90
W_OPEN_TWO = 40
W_CLOSED_TWO = 8
W_SINGLE = 2


class SearchTimeout(Exception):
    pass


def _opp(color):
    return 3 - color


def _in_bounds(r, c):
    return 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE


def check_five(board, r, c, color):
    """True if placing `color` at (r,c) completes five (call after board[r][c]=color)."""
    for dr, dc in DIRS:
        line = 1
        for sign in (-1, 1):
            k = 1
            while True:
                rr, cc = r + dr * sign * k, c + dc * sign * k
                if not _in_bounds(rr, cc) or board[rr][cc] != color:
                    break
                line += 1
                k += 1
        if line >= 5:
            return True
    return False


def find_immediate_win(board, color):
    """Return (r,c) if any empty cell wins for `color`, else None."""
    for r, c in threat_space_candidates(board):
        if board[r][c] != EMPTY:
            continue
        board[r][c] = color
        won = check_five(board, r, c, color)
        board[r][c] = EMPTY
        if won:
            return (r, c)
    return None


def board_has_stones(board):
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if board[r][c] != EMPTY:
                return True
    return False


def threat_space_candidates(board):
    """
    Threat-space: empty intersections within Chebyshev distance 2 of any stone.
    If the board is empty, prefer the center area.
    """
    out = []
    if not board_has_stones(board):
        for r in range(7, 12):
            for c in range(7, 12):
                if board[r][c] == EMPTY:
                    out.append((r, c))
        return out if out else [(BOARD_SIZE // 2, BOARD_SIZE // 2)]

    seen = set()
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if board[r][c] == EMPTY:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    rr, cc = r + dr, c + dc
                    if not _in_bounds(rr, cc) or board[rr][cc] != EMPTY:
                        continue
                    if (rr, cc) not in seen:
                        seen.add((rr, cc))
                        out.append((rr, cc))
    return out


def _segment_pattern_score(length, left_empty, right_empty):
    if length >= 5:
        return W_FIVE
    if length == 4:
        if left_empty and right_empty:
            return W_OPEN_FOUR
        if left_empty or right_empty:
            return W_CLOSED_FOUR
        return 0
    if length == 3:
        if left_empty and right_empty:
            return W_OPEN_THREE
        if left_empty or right_empty:
            return W_CLOSED_THREE
        return 0
    if length == 2:
        if left_empty and right_empty:
            return W_OPEN_TWO
        if left_empty or right_empty:
            return W_CLOSED_TWO
        return 0
    if length == 1:
        if left_empty and right_empty:
            return W_SINGLE * 2
        if left_empty or right_empty:
            return W_SINGLE
    return 0


def evaluate_side_segments(board, color):
    """Sum pattern scores for all contiguous runs of `color` along four directions."""
    total = 0
    for dr, dc in DIRS:
        for r in range(BOARD_SIZE):
            for c in range(BOARD_SIZE):
                if board[r][c] != color:
                    continue
                pr, pc = r - dr, c - dc
                if _in_bounds(pr, pc) and board[pr][pc] == color:
                    continue
                length = 0
                rr, cc = r, c
                while _in_bounds(rr, cc) and board[rr][cc] == color:
                    length += 1
                    rr += dr
                    cc += dc
                lr, lc = r - dr, c - dc
                left_empty = _in_bounds(lr, lc) and board[lr][lc] == EMPTY
                right_empty = _in_bounds(rr, cc) and board[rr][cc] == EMPTY
                total += _segment_pattern_score(length, left_empty, right_empty)
    return total


def evaluate_board(board, root_color):
    """Heuristic from perspective of root_color (positive = good for root)."""
    opp = _opp(root_color)
    return evaluate_side_segments(board, root_color) - evaluate_side_segments(board, opp)


def tactical_move_order_key(board, r, c, player):
    """Cheap local score for move ordering inside negamax (placed cell is empty before sim)."""
    board[r][c] = player
    key = 0
    o = _opp(player)
    for dr, dc in DIRS:
        my_run = 1
        for sign in (-1, 1):
            k = 1
            while True:
                rr, cc = r + dr * sign * k, c + dc * sign * k
                if not _in_bounds(rr, cc):
                    break
                if board[rr][cc] == player:
                    my_run += 1
                elif board[rr][cc] == EMPTY:
                    break
                else:
                    break
                k += 1
        key += my_run * my_run * 50
        opp_adj = 0
        for sign in (-1, 1):
            k = 1
            while True:
                rr, cc = r + dr * sign * k, c + dc * sign * k
                if not _in_bounds(rr, cc):
                    break
                if board[rr][cc] == o:
                    opp_adj += 1
                elif board[rr][cc] == EMPTY:
                    break
                else:
                    break
                k += 1
        key += opp_adj * opp_adj * 40
    board[r][c] = EMPTY
    return key


def ordered_threat_moves(board, color, moves, limit):
    if not moves:
        return []
    move_set = set(moves)
    win = find_immediate_win(board, color)
    scored = [(tactical_move_order_key(board, r, c, color), r, c) for r, c in moves]
    scored.sort(key=lambda t: -t[0])
    out = []
    seen = set()
    if win is not None and win in move_set:
        out.append(win)
        seen.add(win)
    for _, r, c in scored:
        if (r, c) in seen:
            continue
        out.append((r, c))
        if len(out) >= limit:
            break
    return out


def minimax(board, depth, alpha, beta, color, root_color, deadline, nodes):
    """Alpha-beta minimax; leaf score is always root-relative (positive favors root_color)."""
    nodes[0] += 1
    if nodes[0] % NODE_CHECK_INTERVAL == 0 and time.perf_counter() > deadline:
        raise SearchTimeout()
    if time.perf_counter() > deadline:
        raise SearchTimeout()

    o = _opp(color)

    if depth == 0:
        if find_immediate_win(board, color) is not None:
            return INF if color == root_color else -INF
        return evaluate_board(board, root_color)

    raw_moves = threat_space_candidates(board)
    if not raw_moves:
        return 0

    moves = ordered_threat_moves(board, color, raw_moves, MAX_CANDIDATES)

    if color == root_color:
        best = -INF
        for r, c in moves:
            if board[r][c] != EMPTY:
                continue
            board[r][c] = color
            try:
                if check_five(board, r, c, color):
                    sc = INF
                else:
                    sc = minimax(board, depth - 1, alpha, beta, o, root_color, deadline, nodes)
            finally:
                board[r][c] = EMPTY
            if sc > best:
                best = sc
            if best > alpha:
                alpha = best
            if alpha >= beta:
                break
        return best

    best = INF
    for r, c in moves:
        if board[r][c] != EMPTY:
            continue
        board[r][c] = color
        try:
            if check_five(board, r, c, color):
                sc = -INF
            else:
                sc = minimax(board, depth - 1, alpha, beta, o, root_color, deadline, nodes)
        finally:
            board[r][c] = EMPTY
        if sc < best:
            best = sc
        if best < beta:
            beta = best
        if alpha >= beta:
            break
    return best


def minimax_root(board, depth, root_color, deadline, ordered_moves, nodes):
    if time.perf_counter() > deadline:
        raise SearchTimeout()
    o = _opp(root_color)
    best_move = ordered_moves[0]
    best_val = -INF
    alpha = -INF
    beta = INF
    for r, c in ordered_moves:
        if board[r][c] != EMPTY:
            continue
        board[r][c] = root_color
        try:
            if check_five(board, r, c, root_color):
                val = INF
            else:
                val = minimax(board, depth - 1, alpha, beta, o, root_color, deadline, nodes)
        finally:
            board[r][c] = EMPTY
        if val > best_val:
            best_val = val
            best_move = (r, c)
        if val > alpha:
            alpha = val
        if alpha >= beta:
            break
    return best_val, best_move


def choose_move_search(board, my_color):
    """
    Threat-space candidate generation + pattern heuristic ordering +
    iterative deepening minimax with alpha-beta under a wall-clock budget.
    """
    deadline = time.perf_counter() + MOVE_TIME_BUDGET_SEC
    work = [list(row) for row in board]
    opp = _opp(my_color)

    w = find_immediate_win(work, my_color)
    if w:
        return w
    b = find_immediate_win(work, opp)
    if b:
        return b

    cands = threat_space_candidates(work)
    if not cands:
        return (BOARD_SIZE // 2, BOARD_SIZE // 2)

    rankings = [[0 for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
    rank_cells(work, rankings, my_color, opp)
    opp_rank = [[0 for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
    rank_cells(work, opp_rank, opp, my_color)

    def combined_score(r, c):
        return rankings[r][c] * 1.0 + opp_rank[r][c] * 0.98 + random.random() * 0.01

    sorted_cands = sorted(cands, key=lambda m: -combined_score(m[0], m[1]))
    ordered = sorted_cands[: max(MAX_CANDIDATES, 1)]

    if time.perf_counter() >= deadline:
        return ordered[0]

    nodes = [0]
    best = ordered[0]

    depth = 1
    while depth <= MAX_DEPTH_CAP:
        if time.perf_counter() >= deadline:
            break
        try:
            _, mv = minimax_root(work, depth, my_color, deadline, ordered, nodes)
            if mv is not None:
                best = mv
        except SearchTimeout:
            break
        depth += 1

    return best


class GomokuBot:
    def __init__(self, token, username):
        self.token = token
        self.username = username
        self.ws = None
        self.game_id = None
        self.my_color = None
        self.board = None
        self.game_over = False
        
    async def connect(self):
        """Connect to the game server"""
        print(f"[BOT] Connecting to {SERVER_URL}...", flush=True)
        sys.stderr.write(f"[BOT-STDERR] Connecting to {SERVER_URL}...\n")
        sys.stderr.flush()
        self.ws = await websockets.connect(SERVER_URL)
        
        # Authenticate
        await self.send_message({
            "type": "authenticate",
            "token": self.token
        })
        
        print(f"[BOT] Connected as {self.username}", flush=True)
        sys.stderr.write(f"[BOT-STDERR] Connected as {self.username}\n")
        sys.stderr.flush()
    
    async def send_message(self, message):
        """Send a message to the server"""
        await self.ws.send(json.dumps(message))
    
    async def handle_message(self, message):
        """Handle incoming messages from server"""
        msg_type = message.get("type")
        
        if msg_type == "authenticated":
            print(f"[BOT] Authenticated successfully")
            print(f"[BOT] Waiting for tournament runner to initiate games...")
            # Don't automatically create or join games
            # Wait for force_game from tournament runner
        
        elif msg_type == "ping":
            # Respond to heartbeat
            await self.send_message({"type": "pong"})
        
        elif msg_type == "lobby_update":
            # Don't auto-join games - wait for tournament runner
            pass
        
        elif msg_type == "game_created":
            self.game_id = message.get("game_id")
            self.my_color = message.get("color")
            color_name = "Black" if self.my_color == BLACK else "White"
            print(f"[BOT] Created game {self.game_id}, playing as {color_name}")
            print(f"[BOT] Waiting for opponent...")
        
        elif msg_type == "game_started":
            self.game_id = message.get("game_id")
            self.my_color = message.get("your_color")
            player1 = message.get("player1")
            player2 = message.get("player2")
            opponent = player2 if player1 == self.username else player1
            color_name = "Black" if self.my_color == BLACK else "White"
            print(f"[BOT] Game started! Playing as {color_name} against {opponent}")
        
        elif msg_type == "game_update":
            self.board = message.get("board")
            current_turn = message.get("current_turn")
            game_over = message.get("game_over")
            winner = message.get("winner")
            
            if game_over:
                self.game_over = True
                if winner == self.username:
                    print(f"[BOT] Victory! I won the game!")
                elif winner:
                    print(f"[BOT] Defeat. {winner} won the game.")
                else:
                    print(f"[BOT] Game ended in a draw.")
                
                # Wait a bit then find another game
                await asyncio.sleep(1)
                self.reset_game_state()
                await self.find_game()
            
            elif current_turn == self.my_color:
                # It's my turn
                move = self.choose_move(self.board, self.my_color, self.game_id)
                if move:
                    row, col = move
                    print(f"[BOT] Making move: ({row},{col})")
                    await self.send_message({
                        "type": "make_move",
                        "row": row,
                        "col": col
                    })
        
        elif msg_type == "error":
            code = message.get("code")
            error_message = message.get("message")
            print(f"[ERROR] {code}: {error_message}")

            if code == "ALREADY_IN_GAME":
                # Already in a game, wait for updates
                pass
            elif code == "GAME_NOT_FOUND":
                # Game not found, try to find another
                await self.find_game()

    async def find_game(self):
        """Create a new game and wait for an opponent"""
        print(f"[BOT] Creating new game")
        await self.send_message({"type": "create_game"})
    
    def reset_game_state(self):
        """Reset game state for next game"""
        self.game_id = None
        self.my_color = None
        self.board = None
        self.game_over = False
    
    def choose_move(self, board, my_color, game_id):
        """
        Threat-space candidates, pattern-based ordering (rank_cells), then iterative-deepening
        minimax with alpha-beta and segment pattern evaluation within the time budget.
        """
        return choose_move_search(board, my_color)
    
    async def run(self):
        """Main bot loop"""
        self.reset_game_state()
        await self.connect()
        
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    await self.handle_message(data)
                except json.JSONDecodeError as e:
                    print(f"[ERROR] Failed to parse message: {e}")
                except Exception as e:
                    print(f"[ERROR] Error handling message: {e}")
        except websockets.exceptions.ConnectionClosed:
            print(f"[BOT] Connection closed")
        except Exception as e:
            print(f"[ERROR] Unexpected error: {e}")
        finally:
            if self.ws:
                await self.ws.close()


def add_random_rankings(board, rankings):
    """Add small random values to rankings to break ties"""
    for row in range(BOARD_SIZE):
        for col in range(BOARD_SIZE):
            if board[row][col] == EMPTY:
                rankings[row][col] += round(random.random(), 4)


def rank_cells(board, rankings, my_color, opponent_color):
    """
    Rank all empty cells by examining patterns in all 8 directions.
    For each empty cell, look at nearby stones in each direction and score
    the position based on offensive and defensive patterns.
    """
    # 8 directions: right, down-right, down, down-left, left, up-left, up, up-right
    wheel = [[0,1], [1,1], [1,0], [1,-1], [0,-1], [-1,-1], [-1,0], [-1,1]]
    
    for row in range(BOARD_SIZE):
        for col in range(BOARD_SIZE):
            # Only rank empty cells
            if board[row][col] == EMPTY:
                # Check patterns in all 8 directions
                for v, h in wheel:
                    # Create a view of nearby cells in this direction
                    # Pattern: [4 back][3 back][2 back][1 back][CURRENT][1 ahead][2 ahead][3 ahead][4 ahead]
                    nearby = [3, 3, 3, 3, 0, 3, 3, 3, 3]  # Default to walls (3)
                    
                    # Look backward (indices 0-3)
                    for i in range(1, 5):
                        r, c = row - v * i, col - h * i
                        if 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE:
                            nearby[4 - i] = board[r][c]
                    
                    # Look forward (indices 5-8)
                    for i in range(1, 5):
                        r, c = row + v * i, col + h * i
                        if 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE:
                            nearby[4 + i] = board[r][c]
                    
                    # Score this pattern
                    rankings[row][col] += score_row(nearby, my_color, opponent_color)


def score_row(r, my_color, opponent_color):
    """
    Score a 9-cell pattern where index 4 is the empty cell being evaluated.
    
    Pattern indices: [0][1][2][3][4][5][6][7][8]
    where [4] is the current empty position
    
    Values:
    - 0 = empty
    - my_color = my stone (1 or 2)
    - opponent_color = opponent's stone (2 or 1)
    - 3 = wall/edge
    """
    
    # === IMMEDIATE WINNING MOVES (10000+ points) ===
    # Four of my stones in a row - I can win immediately
    if r[0] == my_color and r[1] == my_color and r[2] == my_color and r[3] == my_color:
        return 10000
    elif r[1] == my_color and r[2] == my_color and r[3] == my_color and r[5] == my_color:
        return 10000
    elif r[2] == my_color and r[3] == my_color and r[5] == my_color and r[6] == my_color:
        return 5000
    
    # === BLOCK OPPONENT'S IMMEDIATE WIN (8000+ points) ===
    # Four opponent stones in a row - must block
    elif r[0] == opponent_color and r[1] == opponent_color and r[2] == opponent_color and r[3] == opponent_color:
        return 8000
    elif r[1] == opponent_color and r[2] == opponent_color and r[3] == opponent_color and r[5] == opponent_color:
        return 8000
    elif r[2] == opponent_color and r[3] == opponent_color and r[5] == opponent_color and r[6] == opponent_color:
        return 4000
    
    # === FORCING MOVES - Create double threats (1000 points) ===
    # Three of my stones with both ends open - creates forcing move
    elif r[0] == 0 and r[1] == my_color and r[2] == my_color and r[3] == my_color and r[5] == 0:
        return 1000
    elif r[1] == 0 and r[2] == my_color and r[3] == my_color and r[5] == my_color and r[6] == 0:
        return 1000
    
    # === BLOCK OPPONENT'S FORCING MOVES (800 points) ===
    elif r[0] == 0 and r[1] == opponent_color and r[2] == opponent_color and r[3] == opponent_color and r[5] == 0:
        return 800
    elif r[1] == 0 and r[2] == opponent_color and r[3] == opponent_color and r[5] == opponent_color and r[6] == 0:
        return 800
    
    # === BUILD STRONG ATTACKS - Three in a row patterns (100 points) ===
    # Three of my stones with one end open
    elif r[0] == 0 and r[1] == my_color and r[2] == my_color and r[3] == my_color:
        return 100
    elif r[0] == my_color and r[1] == 0 and r[2] == my_color and r[3] == my_color:
        return 100
    elif r[0] == my_color and r[1] == my_color and r[2] == 0 and r[3] == my_color:
        return 100
    elif r[0] == my_color and r[1] == my_color and r[2] == my_color and r[3] == 0:
        return 100
    elif r[1] == 0 and r[2] == my_color and r[3] == my_color and r[5] == my_color:
        return 100
    elif r[1] == my_color and r[2] == 0 and r[3] == my_color and r[5] == my_color:
        return 100
    elif r[1] == my_color and r[2] == my_color and r[3] == 0 and r[5] == my_color:
        return 100
    elif r[1] == my_color and r[2] == my_color and r[3] == my_color and r[5] == 0:
        return 100
    elif r[2] == 0 and r[3] == my_color and r[5] == my_color and r[6] == my_color:
        return 50
    elif r[2] == my_color and r[3] == 0 and r[5] == my_color and r[6] == my_color:
        return 50
    elif r[2] == my_color and r[3] == my_color and r[5] == 0 and r[6] == my_color:
        return 50
    elif r[2] == my_color and r[3] == my_color and r[5] == my_color and r[6] == 0:
        return 50
    
    # Three of my stones with gaps but open ends
    elif r[0] == 0 and r[1] == 0 and r[2] == my_color and r[3] == my_color and r[5] == 0:
        return 100
    elif r[0] == 0 and r[1] == my_color and r[2] == 0 and r[3] == my_color and r[5] == 0:
        return 100
    elif r[0] == 0 and r[1] == my_color and r[2] == my_color and r[3] == 0 and r[5] == 0:
        return 100
    elif r[1] == 0 and r[2] == 0 and r[3] == my_color and r[5] == my_color and r[6] == 0:
        return 100
    elif r[1] == 0 and r[2] == my_color and r[3] == 0 and r[5] == my_color and r[6] == 0:
        return 100
    elif r[1] == 0 and r[2] == my_color and r[3] == my_color and r[5] == 0 and r[6] == 0:
        return 100
    
    # === BLOCK OPPONENT'S ATTACKS - Three in a row patterns (80 points) ===
    elif r[0] == 0 and r[1] == opponent_color and r[2] == opponent_color and r[3] == opponent_color:
        return 80
    elif r[0] == opponent_color and r[1] == 0 and r[2] == opponent_color and r[3] == opponent_color:
        return 80
    elif r[0] == opponent_color and r[1] == opponent_color and r[2] == 0 and r[3] == opponent_color:
        return 80
    elif r[0] == opponent_color and r[1] == opponent_color and r[2] == opponent_color and r[3] == 0:
        return 80
    elif r[1] == 0 and r[2] == opponent_color and r[3] == opponent_color and r[5] == opponent_color:
        return 80
    elif r[1] == opponent_color and r[2] == 0 and r[3] == opponent_color and r[5] == opponent_color:
        return 80
    elif r[1] == opponent_color and r[2] == opponent_color and r[3] == 0 and r[5] == opponent_color:
        return 80
    elif r[1] == opponent_color and r[2] == opponent_color and r[3] == opponent_color and r[5] == 0:
        return 80
    elif r[2] == 0 and r[3] == opponent_color and r[5] == opponent_color and r[6] == opponent_color:
        return 40
    elif r[2] == opponent_color and r[3] == 0 and r[5] == opponent_color and r[6] == opponent_color:
        return 40
    elif r[2] == opponent_color and r[3] == opponent_color and r[5] == 0 and r[6] == opponent_color:
        return 40
    elif r[2] == opponent_color and r[3] == opponent_color and r[5] == opponent_color and r[6] == 0:
        return 40
    
    # Three opponent stones with gaps
    elif r[0] == 0 and r[1] == 0 and r[2] == opponent_color and r[3] == opponent_color and r[5] == 0:
        return 80
    elif r[0] == 0 and r[1] == opponent_color and r[2] == 0 and r[3] == opponent_color and r[5] == 0:
        return 80
    elif r[0] == 0 and r[1] == opponent_color and r[2] == opponent_color and r[3] == 0 and r[5] == 0:
        return 80
    elif r[1] == 0 and r[2] == 0 and r[3] == opponent_color and r[5] == opponent_color and r[6] == 0:
        return 80
    elif r[1] == 0 and r[2] == opponent_color and r[3] == 0 and r[5] == opponent_color and r[6] == 0:
        return 80
    elif r[1] == 0 and r[2] == opponent_color and r[3] == opponent_color and r[5] == 0 and r[6] == 0:
        return 80
    
    # === BUILD TWO IN A ROW - My stones (10 points) ===
    # Two of my stones with open space around
    elif r[0] == my_color and r[1] == my_color and r[2] == 0 and r[3] == 0:
        return 10
    elif r[0] == my_color and r[1] == 0 and r[2] == my_color and r[3] == 0:
        return 10
    elif r[0] == my_color and r[1] == 0 and r[2] == 0 and r[3] == my_color:
        return 10
    elif r[0] == 0 and r[1] == my_color and r[2] == my_color and r[3] == 0:
        return 10
    elif r[0] == 0 and r[1] == my_color and r[2] == 0 and r[3] == my_color:
        return 10
    elif r[0] == 0 and r[1] == 0 and r[2] == my_color and r[3] == my_color:
        return 10
    
    # Shifted window
    elif r[1] == my_color and r[2] == my_color and r[3] == 0 and r[5] == 0:
        return 10
    elif r[1] == my_color and r[2] == 0 and r[3] == my_color and r[5] == 0:
        return 10
    elif r[1] == my_color and r[2] == 0 and r[3] == 0 and r[5] == my_color:
        return 10
    elif r[1] == 0 and r[2] == my_color and r[3] == my_color and r[5] == 0:
        return 10
    elif r[1] == 0 and r[2] == my_color and r[3] == 0 and r[5] == my_color:
        return 10
    elif r[1] == 0 and r[2] == 0 and r[3] == my_color and r[5] == my_color:
        return 10
    
    # Shifted window again
    elif r[2] == my_color and r[3] == my_color and r[5] == 0 and r[6] == 0:
        return 10
    elif r[2] == my_color and r[3] == 0 and r[5] == my_color and r[6] == 0:
        return 10
    elif r[2] == my_color and r[3] == 0 and r[5] == 0 and r[6] == my_color:
        return 5
    elif r[2] == 0 and r[3] == my_color and r[5] == my_color and r[6] == 0:
        return 5
    elif r[2] == 0 and r[3] == my_color and r[5] == 0 and r[6] == my_color:
        return 10
    elif r[2] == 0 and r[3] == 0 and r[5] == my_color and r[6] == my_color:
        return 10
    
    # === BLOCK TWO IN A ROW - Opponent stones (10 points) ===
    elif r[0] == opponent_color and r[1] == opponent_color and r[2] == 0 and r[3] == 0:
        return 10
    elif r[0] == opponent_color and r[1] == 0 and r[2] == opponent_color and r[3] == 0:
        return 10
    elif r[0] == opponent_color and r[1] == 0 and r[2] == 0 and r[3] == opponent_color:
        return 10
    elif r[0] == 0 and r[1] == opponent_color and r[2] == opponent_color and r[3] == 0:
        return 10
    elif r[0] == 0 and r[1] == opponent_color and r[2] == 0 and r[3] == opponent_color:
        return 10
    elif r[0] == 0 and r[1] == 0 and r[2] == opponent_color and r[3] == opponent_color:
        return 10
    
    # Shifted window
    elif r[1] == opponent_color and r[2] == opponent_color and r[3] == 0 and r[5] == 0:
        return 10
    elif r[1] == opponent_color and r[2] == 0 and r[3] == opponent_color and r[5] == 0:
        return 10
    elif r[1] == opponent_color and r[2] == 0 and r[3] == 0 and r[5] == opponent_color:
        return 10
    elif r[1] == 0 and r[2] == opponent_color and r[3] == opponent_color and r[5] == 0:
        return 10
    elif r[1] == 0 and r[2] == opponent_color and r[3] == 0 and r[5] == opponent_color:
        return 10
    elif r[1] == 0 and r[2] == 0 and r[3] == opponent_color and r[5] == opponent_color:
        return 10
    
    # Shifted window again
    elif r[2] == opponent_color and r[3] == opponent_color and r[5] == 0 and r[6] == 0:
        return 10
    elif r[2] == opponent_color and r[3] == 0 and r[5] == opponent_color and r[6] == 0:
        return 10
    elif r[2] == opponent_color and r[3] == 0 and r[5] == 0 and r[6] == opponent_color:
        return 5
    elif r[2] == 0 and r[3] == opponent_color and r[5] == opponent_color and r[6] == 0:
        return 5
    elif r[2] == 0 and r[3] == opponent_color and r[5] == 0 and r[6] == opponent_color:
        return 10
    elif r[2] == 0 and r[3] == 0 and r[5] == opponent_color and r[6] == opponent_color:
        return 10
    
    # === SINGLE STONE EXTENSIONS (1 point) ===
    # One of my stones with space to build
    elif r[0] == my_color and r[1] == 0 and r[2] == 0 and r[3] == 0:
        return 1
    elif r[0] == 0 and r[1] == my_color and r[2] == 0 and r[3] == 0:
        return 1
    elif r[0] == 0 and r[1] == 0 and r[2] == my_color and r[3] == 0:
        return 1
    elif r[0] == 0 and r[1] == 0 and r[2] == 0 and r[3] == my_color:
        return 1
    elif r[1] == my_color and r[2] == 0 and r[3] == 0 and r[5] == 0:
        return 1
    elif r[1] == 0 and r[2] == my_color and r[3] == 0 and r[5] == 0:
        return 1
    elif r[1] == 0 and r[2] == 0 and r[3] == my_color and r[5] == 0:
        return 1
    elif r[1] == 0 and r[2] == 0 and r[3] == 0 and r[5] == my_color:
        return 1
    elif r[2] == my_color and r[3] == 0 and r[5] == 0 and r[6] == 0:
        return 1
    elif r[2] == 0 and r[3] == my_color and r[5] == 0 and r[6] == 0:
        return 1
    
    # One opponent stone
    elif r[0] == opponent_color and r[1] == 0 and r[2] == 0 and r[3] == 0:
        return 1
    elif r[0] == 0 and r[1] == opponent_color and r[2] == 0 and r[3] == 0:
        return 1
    elif r[0] == 0 and r[1] == 0 and r[2] == opponent_color and r[3] == 0:
        return 1
    elif r[0] == 0 and r[1] == 0 and r[2] == 0 and r[3] == opponent_color:
        return 1
    elif r[1] == opponent_color and r[2] == 0 and r[3] == 0 and r[5] == 0:
        return 1
    elif r[1] == 0 and r[2] == opponent_color and r[3] == 0 and r[5] == 0:
        return 1
    elif r[1] == 0 and r[2] == 0 and r[3] == opponent_color and r[5] == 0:
        return 1
    elif r[1] == 0 and r[2] == 0 and r[3] == 0 and r[5] == opponent_color:
        return 1
    elif r[2] == opponent_color and r[3] == 0 and r[5] == 0 and r[6] == 0:
        return 1
    elif r[2] == 0 and r[3] == opponent_color and r[5] == 0 and r[6] == 0:
        return 1
    
    else:
        return 0


def pick_highest(board, rankings):
    """
    Select the move with the highest ranking score.
    Returns (row, col) tuple or None if no valid moves.
    """
    highest = 0
    best_row = 0
    best_col = 0
    
    for row in range(BOARD_SIZE):
        for col in range(BOARD_SIZE):
            if rankings[row][col] > highest:
                best_row = row
                best_col = col
                highest = rankings[row][col]
    
    # Verify the selected position is actually empty
    if board[best_row][best_col] == EMPTY:
        return (best_row, best_col)
    else:
        # Fallback: find any valid move
        for row in range(BOARD_SIZE):
            for col in range(BOARD_SIZE):
                if board[row][col] == EMPTY:
                    return (row, col)
        return None


async def main():
    # Check if token and username are provided
    if not TOKEN or not USERNAME:
        print("ERROR: Please set your TOKEN and USERNAME in the script")
        print("Get your token by running:")
        print("  curl -X POST 'http://localhost:8000/register?username=YOUR_USERNAME&is_bot=true'")
        sys.exit(1)
    
    bot = GomokuBot(TOKEN, USERNAME)
    
    while True:
        try:
            await bot.run()
        except Exception as e:
            print(f"[ERROR] Bot crashed: {e}")
        
        # Wait before reconnecting
        print("[BOT] Reconnecting in 5 seconds...")
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())

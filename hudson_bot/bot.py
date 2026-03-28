import asyncio
import websockets
import json
import random
import sys
import os

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

# Minimax tuning knobs
WIN_SCORE = 10_000_000
MAX_CANDIDATES = 12
NEIGHBOR_RADIUS = 2
MIDGAME_DEPTH = 2
ENDGAME_DEPTH = 3

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
        print(f"[BOT] Connecting to {SERVER_URL}...")
        self.ws = await websockets.connect(SERVER_URL, max_size=10 * 1024 * 1024)
        
        # Authenticate
        await self.send_message({
            "type": "authenticate",
            "token": self.token
        })
        
        print(f"[BOT] Connected as {self.username}")
    
    async def send_message(self, message):
        """Send a message to the server"""
        await self.ws.send(json.dumps(message))
    
    async def handle_message(self, message):
        """Handle incoming messages from server"""
        msg_type = message.get("type")
        
        if msg_type == "authenticated":
            print(f"[BOT] Authenticated successfully")
            # Randomly decide to create or join a game
            await self.find_game()
        
        elif msg_type == "ping":
            # Respond to heartbeat
            await self.send_message({"type": "pong"})
        
        elif msg_type == "lobby_update":
            # Received lobby games list
            games = message.get("games", [])
            if games:
                # Join the oldest waiting game
                oldest_game = games[0]
                print(f"[BOT] Joining game {oldest_game['game_id']} created by {oldest_game['creator']}")
                await self.send_message({
                    "type": "join_game",
                    "game_id": oldest_game["game_id"]
                })
            else:
                # No games available, create one
                print(f"[BOT] No games available, creating new game")
                await self.send_message({"type": "create_game"})
        
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
                await asyncio.sleep(2)
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
        Choose the best move with depth-limited minimax and alpha-beta pruning.

        Search breadth is restricted to high-value candidate moves near existing stones,
        ordered by the existing heuristic ranker for better pruning.
        """
        opponent_color = 3 - my_color

        if is_board_empty(board):
            center = BOARD_SIZE // 2
            return (center, center)

        empty_count = count_empty_cells(board)
        depth = ENDGAME_DEPTH if empty_count <= 45 else MIDGAME_DEPTH

        candidate_moves = generate_candidate_moves(
            board,
            my_color,
            opponent_color,
            MAX_CANDIDATES,
            NEIGHBOR_RADIUS,
        )

        if not candidate_moves:
            return find_first_empty(board)

        # Tactical fast-path: take immediate win if available.
        for row, col in candidate_moves:
            board[row][col] = my_color
            winning_now = is_winning_move(board, row, col, my_color)
            board[row][col] = EMPTY
            if winning_now:
                return (row, col)

        best_score = -float("inf")
        best_moves = []

        for row, col in candidate_moves:
            board[row][col] = my_color
            score = minimax(
                board,
                depth - 1,
                -float("inf"),
                float("inf"),
                False,
                my_color,
                opponent_color,
                row,
                col,
            )
            board[row][col] = EMPTY

            if score > best_score:
                best_score = score
                best_moves = [(row, col)]
            elif score == best_score:
                best_moves.append((row, col))

        if best_moves:
            return random.choice(best_moves)

        return find_first_empty(board)
    
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


def minimax(board, depth, alpha, beta, is_maximizing, my_color, opponent_color, last_row, last_col):
    """Minimax search with alpha-beta pruning over a reduced candidate set."""
    last_player = opponent_color if is_maximizing else my_color
    if is_winning_move(board, last_row, last_col, last_player):
        # Prefer faster wins and slower losses.
        if last_player == my_color:
            return WIN_SCORE + depth
        return -WIN_SCORE - depth

    if depth == 0 or is_board_full(board):
        return evaluate_board(board, my_color, opponent_color)

    if is_maximizing:
        value = -float("inf")
        moves = generate_candidate_moves(
            board,
            my_color,
            opponent_color,
            MAX_CANDIDATES,
            NEIGHBOR_RADIUS,
        )
        if not moves:
            return evaluate_board(board, my_color, opponent_color)

        for row, col in moves:
            board[row][col] = my_color
            score = minimax(
                board,
                depth - 1,
                alpha,
                beta,
                False,
                my_color,
                opponent_color,
                row,
                col,
            )
            board[row][col] = EMPTY
            value = max(value, score)
            alpha = max(alpha, value)
            if beta <= alpha:
                break
        return value

    value = float("inf")
    moves = generate_candidate_moves(
        board,
        opponent_color,
        my_color,
        MAX_CANDIDATES,
        NEIGHBOR_RADIUS,
    )
    if not moves:
        return evaluate_board(board, my_color, opponent_color)

    for row, col in moves:
        board[row][col] = opponent_color
        score = minimax(
            board,
            depth - 1,
            alpha,
            beta,
            True,
            my_color,
            opponent_color,
            row,
            col,
        )
        board[row][col] = EMPTY
        value = min(value, score)
        beta = min(beta, value)
        if beta <= alpha:
            break
    return value


def evaluate_board(board, my_color, opponent_color):
    """Static board evaluation used at minimax depth cutoff."""
    my_best, my_top_sum = best_and_top_sum_scores(board, my_color, opponent_color, top_n=3)
    opp_best, opp_top_sum = best_and_top_sum_scores(board, opponent_color, my_color, top_n=3)

    # Emphasize strongest tactical moves while still considering broader pressure.
    return (my_best * 3 + my_top_sum) - (opp_best * 3 + opp_top_sum)


def best_and_top_sum_scores(board, my_color, opponent_color, top_n=3):
    rankings = [[0 for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
    rank_cells(board, rankings, my_color, opponent_color)

    scores = []
    for row in range(BOARD_SIZE):
        for col in range(BOARD_SIZE):
            if board[row][col] == EMPTY:
                scores.append(rankings[row][col])

    if not scores:
        return (0, 0)

    scores.sort(reverse=True)
    return (scores[0], sum(scores[:top_n]))


def generate_candidate_moves(board, active_color, passive_color, max_candidates, neighbor_radius):
    """
    Generate high-value candidate moves for the current player.

    Restricting search to nearby/high-score cells keeps minimax tractable on 19x19.
    """
    rankings = [[0 for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
    rank_cells(board, rankings, active_color, passive_color)

    if is_board_empty(board):
        center = BOARD_SIZE // 2
        return [(center, center)]

    scored_moves = []
    center = BOARD_SIZE // 2

    for row in range(BOARD_SIZE):
        for col in range(BOARD_SIZE):
            if board[row][col] != EMPTY:
                continue
            if not has_neighbor(board, row, col, neighbor_radius):
                continue

            # Mild center bias to stabilize opening and tie-break ordering.
            dist = abs(row - center) + abs(col - center)
            score = rankings[row][col] - (dist * 0.01)
            scored_moves.append((score, row, col))

    if not scored_moves:
        for row in range(BOARD_SIZE):
            for col in range(BOARD_SIZE):
                if board[row][col] == EMPTY:
                    dist = abs(row - center) + abs(col - center)
                    score = rankings[row][col] - (dist * 0.01)
                    scored_moves.append((score, row, col))

    scored_moves.sort(reverse=True)
    top_moves = scored_moves[:max_candidates]
    return [(row, col) for _, row, col in top_moves]


def has_neighbor(board, row, col, radius):
    """True if any stone exists within the local neighborhood around a cell."""
    for dr in range(-radius, radius + 1):
        for dc in range(-radius, radius + 1):
            if dr == 0 and dc == 0:
                continue

            nr = row + dr
            nc = col + dc
            if 0 <= nr < BOARD_SIZE and 0 <= nc < BOARD_SIZE and board[nr][nc] != EMPTY:
                return True
    return False


def is_board_empty(board):
    for row in range(BOARD_SIZE):
        for col in range(BOARD_SIZE):
            if board[row][col] != EMPTY:
                return False
    return True


def is_board_full(board):
    for row in range(BOARD_SIZE):
        for col in range(BOARD_SIZE):
            if board[row][col] == EMPTY:
                return False
    return True


def count_empty_cells(board):
    empty_count = 0
    for row in range(BOARD_SIZE):
        for col in range(BOARD_SIZE):
            if board[row][col] == EMPTY:
                empty_count += 1
    return empty_count


def find_first_empty(board):
    for row in range(BOARD_SIZE):
        for col in range(BOARD_SIZE):
            if board[row][col] == EMPTY:
                return (row, col)
    return None


def is_winning_move(board, row, col, color):
    """Check whether the stone at (row, col) completes any 5+ line."""
    if row is None or col is None:
        return False
    if board[row][col] != color:
        return False

    directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
    for dr, dc in directions:
        count = 1
        count += count_in_direction(board, row, col, color, dr, dc)
        count += count_in_direction(board, row, col, color, -dr, -dc)
        if count >= 5:
            return True
    return False


def count_in_direction(board, row, col, color, dr, dc):
    total = 0
    r = row + dr
    c = col + dc

    while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == color:
        total += 1
        r += dr
        c += dc

    return total


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

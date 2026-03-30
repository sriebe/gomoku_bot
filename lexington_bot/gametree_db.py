"""Gametree database for storing and retrieving Gomoku game states and moves."""

import sqlite3
import json
import os
from typing import Optional, Tuple, List
import time

# Board representation constants
BOARD_SIZE = 19
EMPTY = 0
BLACK = 1
WHITE = 2

class GametreeDB:
    """SQLite database for storing game states and their outcomes."""
    
    def __init__(self, db_path: str = "gametree.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize the database with required tables."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            
            # Table for board states
            c.execute('''
                CREATE TABLE IF NOT EXISTS board_states (
                    state_hash TEXT PRIMARY KEY,
                    board_json TEXT NOT NULL,
                    current_turn INTEGER NOT NULL,
                    visit_count INTEGER DEFAULT 0,
                    created_at REAL DEFAULT (julianday('now'))
                )
            ''')
            
            # Table for moves from each state
            c.execute('''
                CREATE TABLE IF NOT EXISTS moves (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    state_hash TEXT NOT NULL,
                    row INTEGER NOT NULL,
                    col INTEGER NOT NULL,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    draws INTEGER DEFAULT 0,
                    total_games INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0.0,
                    FOREIGN KEY (state_hash) REFERENCES board_states(state_hash),
                    UNIQUE(state_hash, row, col)
                )
            ''')
            
            # Table for completed games
            c.execute('''
                CREATE TABLE IF NOT EXISTS games (
                    game_id TEXT PRIMARY KEY,
                    player1 TEXT NOT NULL,
                    player2 TEXT NOT NULL,
                    winner TEXT,
                    outcome TEXT,
                    move_count INTEGER,
                    completed_at REAL DEFAULT (julianday('now'))
                )
            ''')
            
            # Table for game moves sequence
            c.execute('''
                CREATE TABLE IF NOT EXISTS game_moves (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    move_number INTEGER NOT NULL,
                    state_hash TEXT NOT NULL,
                    row INTEGER NOT NULL,
                    col INTEGER NOT NULL,
                    player INTEGER NOT NULL,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    FOREIGN KEY (state_hash) REFERENCES board_states(state_hash)
                )
            ''')
            
            conn.commit()
    
    def _board_to_hash(self, board: List[List[int]]) -> str:
        """Convert board state to a hash string."""
        # Flatten board and convert to string
        flat = []
        for row in board:
            for cell in row:
                flat.append(str(cell))
        return ''.join(flat)
    
    def _board_to_json(self, board: List[List[int]]) -> str:
        """Convert board to JSON string."""
        return json.dumps(board)
    
    def get_or_create_state(self, board: List[List[int]], current_turn: int) -> str:
        """Get state hash, creating the state if it doesn't exist."""
        state_hash = self._board_to_hash(board)
        
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            
            # Try to insert, ignore if exists
            c.execute(
                '''INSERT OR IGNORE INTO board_states (state_hash, board_json, current_turn)
                   VALUES (?, ?, ?)''',
                (state_hash, self._board_to_json(board), current_turn)
            )
            
            # Increment visit count
            c.execute(
                '''UPDATE board_states SET visit_count = visit_count + 1
                   WHERE state_hash = ?''',
                (state_hash,)
            )
            
            conn.commit()
        
        return state_hash
    
    def get_best_move(self, board: List[List[int]], my_color: int) -> Optional[Tuple[int, int]]:
        """Get the best known move for this board state.
        
        Returns (row, col) of the move with highest win rate, or None if no moves known.
        """
        state_hash = self._board_to_hash(board)
        
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            
            # Get best move by win rate (minimum 1 game played)
            c.execute(
                '''SELECT row, col, win_rate, total_games
                   FROM moves
                   WHERE state_hash = ? AND total_games > 0
                   ORDER BY win_rate DESC, total_games DESC
                   LIMIT 1''',
                (state_hash,)
            )
            
            result = c.fetchone()
            
            if result:
                row, col, win_rate, total_games = result
                print(f"[GAMETREE] Found move ({row},{col}) with win_rate={win_rate:.2%} (n={total_games})")
                return (row, col)
        
        return None
    
    def record_move(self, game_id: str, move_number: int, 
                    board: List[List[int]], row: int, col: int, 
                    player: int, current_turn: int):
        """Record a move made during a game."""
        state_hash = self.get_or_create_state(board, current_turn)
        
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            
            # Record the move in game_moves
            c.execute(
                '''INSERT OR IGNORE INTO game_moves 
                   (game_id, move_number, state_hash, row, col, player)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (game_id, move_number, state_hash, row, col, player)
            )
            
            # Ensure the move exists in moves table
            c.execute(
                '''INSERT OR IGNORE INTO moves (state_hash, row, col)
                   VALUES (?, ?, ?)''',
                (state_hash, row, col)
            )
            
            conn.commit()
    
    def record_game_result(self, game_id: str, player1: str, player2: str,
                          winner: Optional[str], outcome: str, 
                          move_history: List[dict], my_username: str):
        """Record the result of a completed game and update move statistics."""
        
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            
            # Record game result
            c.execute(
                '''INSERT OR REPLACE INTO games 
                   (game_id, player1, player2, winner, outcome, move_count)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (game_id, player1, player2, winner, outcome, len(move_history))
            )
            
            # Determine if I won
            i_won = winner == my_username
            i_lost = winner is not None and winner != my_username
            
            # Update statistics for all moves I made in this game
            for move_data in move_history:
                state_hash = move_data.get('state_hash')
                row = move_data.get('row')
                col = move_data.get('col')
                player = move_data.get('player')
                
                if not state_hash or player is None:
                    continue
                
                # Determine if this was my move
                # player is the color (1 or 2), need to map to username
                # For now, we track all moves and can filter later
                
                # Update move statistics
                if i_won:
                    c.execute(
                        '''UPDATE moves 
                           SET wins = wins + 1, 
                               total_games = total_games + 1,
                               win_rate = CAST(wins + 1 AS REAL) / (total_games + 1)
                           WHERE state_hash = ? AND row = ? AND col = ?''',
                        (state_hash, row, col)
                    )
                elif i_lost:
                    c.execute(
                        '''UPDATE moves 
                           SET losses = losses + 1, 
                               total_games = total_games + 1,
                               win_rate = CAST(wins AS REAL) / (total_games + 1)
                           WHERE state_hash = ? AND row = ? AND col = ?''',
                        (state_hash, row, col)
                    )
                else:  # Draw
                    c.execute(
                        '''UPDATE moves 
                           SET draws = draws + 1, 
                               total_games = total_games + 1,
                               win_rate = CAST(wins AS REAL) / (total_games + 1)
                           WHERE state_hash = ? AND row = ? AND col = ?''',
                        (state_hash, row, col)
                    )
            
            conn.commit()
        
        print(f"[GAMETREE] Recorded game result: {outcome}, I {'won' if i_won else 'lost' if i_lost else 'drew'}")
    
    def get_stats(self) -> dict:
        """Get database statistics."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            
            c.execute('SELECT COUNT(*) FROM board_states')
            num_states = c.fetchone()[0]
            
            c.execute('SELECT COUNT(*) FROM moves')
            num_moves = c.fetchone()[0]
            
            c.execute('SELECT COUNT(*) FROM games')
            num_games = c.fetchone()[0]
            
            return {
                'board_states': num_states,
                'moves': num_moves,
                'games': num_games
            }

# Global database instance
gametree_db = GametreeDB()

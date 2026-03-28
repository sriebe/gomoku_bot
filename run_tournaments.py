#!/usr/bin/env python3
"""
Tournament runner script - runs bot matchups via WebSocket without UI.
Usage: python3 run_tournaments.py
"""

import asyncio
import json
import sys
import subprocess
import time
import os
import uuid
from typing import Optional
from dataclasses import dataclass, field
import websockets
import requests
from datetime import datetime


@dataclass
class MatchResult:
    player1: str
    player2: str
    winner: Optional[str]
    outcome_type: str
    game_id: str

@dataclass
class TournamentStats:
    player1: str
    player2: str
    matches: list[MatchResult] = field(default_factory=list)
    
    @property
    def p1_wins(self) -> int:
        return sum(1 for m in self.matches if m.winner == self.player1)
    
    @property
    def p2_wins(self) -> int:
        return sum(1 for m in self.matches if m.winner == self.player2)
    
    @property
    def draws(self) -> int:
        return sum(1 for m in self.matches if m.winner is None)
    
    def print_summary(self):
        print(f"\n{'='*60}")
        print(f"Tournament: {self.player1} vs {self.player2}")
        print(f"{'='*60}")
        print(f"{self.player1} wins: {self.p1_wins}")
        print(f"{self.player2} wins: {self.p2_wins}")
        print(f"Draws: {self.draws}")
        print(f"Total matches: {len(self.matches)}")
        if len(self.matches) > 0:
            p1_pct = (self.p1_wins / len(self.matches)) * 100
            print(f"{self.player1} win rate: {p1_pct:.1f}%")


class TournamentRunner:
    def __init__(self, server_url: str = "ws://localhost:8000/ws"):
        self.server_url = server_url
        self.http_server = server_url.replace("ws://", "http://").replace("wss://", "https://").replace("/ws", "")
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.token: Optional[str] = None  # Tournament runner's auth token
        self.pending_games = {}  # game_id -> (player1, player2)
        self.completed_games = {}
        self.tournaments = {}
        self.bot_processes = []
        self.run_number = 0  # Track current run number
        self.runner_name = f"runner_{str(uuid.uuid4())[:6]}"  # Unique runner name (3-20 chars)
        self.session_id = str(uuid.uuid4())[:4]  # Unique session ID for this tournament run (4 chars)
    
    async def register_tournament_runner(self):
        """Register or get existing tournament runner account"""
        try:
            response = requests.post(
                f"{self.http_server}/register",
                params={"username": self.runner_name, "is_bot": "false"},
                timeout=5
            )
            data = response.json()
            
            if "error" in data:
                # Account already exists, try with a different approach
                # For now, just note that we'll need to handle this
                if "already exists" in data["error"]:
                    print(f"ℹ Tournament runner account already exists")
                    # We'll need to get the token differently - for now, skip
                else:
                    print(f"✗ Registration error: {data['error']}")
                    return False
            else:
                self.token = data.get("token")
                print(f"✓ Registered tournament runner account ({self.runner_name})")
                return True
        except requests.exceptions.ConnectionError:
            print(f"✗ Could not connect to server at {self.http_server}")
            print("  Make sure the server is running!")
            return False
        except Exception as e:
            print(f"✗ Registration failed: {e}")
            return False
    
    async def connect(self):
        """Connect to server as a tournament runner"""
        # First, ensure we have a valid token
        if not self.token:
            await self.register_tournament_runner()
        
        # If registration failed but account exists, we need a fallback
        # For now, generate a temporary token that may work
        if not self.token:
            print("\n⚠ Warning: Could not register tournament runner account")
            print("  Attempting to use a generated token...")
            self.token = str(uuid.uuid4())
        
        try:
            self.websocket = await websockets.connect(self.server_url)
            print(f"\n✓ Connected to server at {self.server_url}")
            # Authenticate as tournament runner
            await self.websocket.send(json.dumps({
                "type": "authenticate",
                "token": self.token
            }))
            response = await self.websocket.recv()
            response_data = json.loads(response)
            
            if "error" in response_data:
                print(f"✗ Authentication failed: {response_data.get('message', 'Unknown error')}")
                sys.exit(1)
            
            print(f"✓ Server response: {response}")
        except Exception as e:
            print(f"✗ Failed to connect: {e}")
            sys.exit(1)
    
    async def check_bot_availability(self, bot_names: list[str]) -> dict[str, bool]:
        """Check which bots are currently available"""
        await self.websocket.send(json.dumps({
            "type": "get_available_bots"
        }))
        
        response = json.loads(await self.websocket.recv())
        bots_list = response.get("bots", [])
        available_bot_names = [bot["username"] for bot in bots_list]
        
        availability = {bot: bot in available_bot_names for bot in bot_names}
        
        # Debug: show unavailable bots
        for bot in bot_names:
            if not availability[bot]:
                print(f"    [DEBUG] {bot} is NOT in available list: {available_bot_names[:10]}...")
        
        return availability
    
    async def force_game(self, player1: str, player2: str) -> Optional[str]:
        """Force a game between two players. Returns game_id if successful."""
        try:
            await self.websocket.send(json.dumps({
                "type": "force_game",
                "player1": player1,
                "player2": player2
            }))
            
            # Wait for forced_game_created response
            for _ in range(10):  # Wait up to 5 seconds
                response = json.loads(await asyncio.wait_for(self.websocket.recv(), timeout=0.5))
                if response.get("type") == "forced_game_created":
                    game_id = response.get("game_id")
                    # Store player names for this game
                    self.pending_games[game_id] = (player1, player2)
                    print(f"  Game created: {player1} vs {player2} (ID: {game_id})")
                    # Join as spectator to receive game updates
                    await self.websocket.send(json.dumps({
                        "type": "spectate",
                        "game_id": game_id
                    }))
                    return game_id
        except asyncio.TimeoutError:
            print(f"  ✗ Timeout creating game {player1} vs {player2}")
        except Exception as e:
            print(f"  ✗ Error creating game: {e}")
        
        return None
    
    async def wait_for_game_result(self, game_id: str, timeout: int = 120) -> Optional[MatchResult]:
        """Wait for a game to complete and capture its result"""
        start_time = datetime.now()
        
        while (datetime.now() - start_time).seconds < timeout:
            try:
                response = json.loads(await asyncio.wait_for(self.websocket.recv(), timeout=1))
                
                # Debug: show message types received
                msg_type = response.get("type")
                if msg_type not in ["ping", "pong", "lobby_update", "active_games_update"]:
                    print(f"    [DEBUG] Received: {msg_type} {list(response.keys())}")
                
                # Server sends game_update with game_over, not game_result
                if msg_type == "game_update" and response.get("game_over"):
                    result_game_id = response.get("game_id")
                    if result_game_id == game_id:
                        winner = response.get("winner")
                        outcome_type = response.get("outcome", "UNKNOWN")
                        # Get player names from stored pending games
                        player1, player2 = self.pending_games.get(game_id, ("unknown", "unknown"))
                        # Clean up pending games
                        if game_id in self.pending_games:
                            del self.pending_games[game_id]
                        
                        match = MatchResult(
                            player1=player1,
                            player2=player2,
                            winner=winner,
                            outcome_type=outcome_type,
                            game_id=game_id
                        )
                        print(f"  ✓ Game complete: {player1} vs {player2} - Winner: {winner or 'None'} ({outcome_type})")
                        return match
            except asyncio.TimeoutError:
                # Just keep waiting
                pass
            except Exception as e:
                print(f"  Error receiving message: {e}")
        
        print(f"  ✗ Game {game_id} did not complete within {timeout}s")
        return None
    
    async def run_tournament(self, player1: str, player2: str, num_matches: int):
        """Run a tournament series between two players"""
        if (player1, player2) in self.tournaments:
            print(f"\n✗ Tournament already running between {player1} and {player2}")
            return
        
        tournament = TournamentStats(player1=player1, player2=player2)
        self.tournaments[(player1, player2)] = tournament
        
        print(f"\n🎮 Starting tournament: {player1} vs {player2} ({num_matches} matches)")
        print(f"{'─'*60}")
        
        for i in range(num_matches):
            print(f"\nMatch {i+1}/{num_matches}:")
            
            # Check availability with retries
            max_retries = 5
            for retry in range(max_retries):
                availability = await self.check_bot_availability([player1, player2])
                if availability[player1] and availability[player2]:
                    break
                if retry == 0:
                    print(f"  ⏳ Bots not yet available, waiting...")
                await asyncio.sleep(3)  # Wait 3 seconds between retries
            
            if not availability[player1] or not availability[player2]:
                print(f"  ✗ Bots still unavailable after {max_retries} retries. Skipping match {i+1}")
                continue
            
            # Start game
            game_id = await self.force_game(player1, player2)
            if not game_id:
                print(f"  ✗ Failed to create game")
                await asyncio.sleep(1)
                continue
            
            # Wait for result
            result = await self.wait_for_game_result(game_id, timeout=120)
            if result:
                tournament.matches.append(result)
            else:
                print(f"  ✗ No result received for game {game_id}")
            
            # Small delay between matches to let server clean up
            if i < num_matches - 1:
                await asyncio.sleep(3)
        
        tournament.print_summary()
        return tournament
    
    @staticmethod
    def generate_token():
        """Generate a unique token for bot authentication"""
        return str(uuid.uuid4())[:8]
    
    def launch_bots(self) -> dict[str, str]:
        """Launch the bots in background processes with unique session names
        
        Returns a mapping of bot base names to their versioned names
        """
        self.run_number += 1
        print(f"\n🤖 Launching bots (run #{self.run_number})...\n")
        
        bots = [
            ("easy_bot", "/Users/mattq/Projects/github.com/gomoku_gargoyles/easy_bot_local_tournament"),
            ("normal_bot", "/Users/mattq/Projects/github.com/gomoku_gargoyles/normal_bot_local_tournament"),
            ("lexington_bot", "/Users/mattq/Projects/github.com/gomoku_gargoyles/lexington_bot"),
        ]
        
        bot_name_mapping = {}
        
        for bot_name, bot_dir in bots:
            # Use session ID to ensure unique usernames across tournament runs
            versioned_name = f"{bot_name}_s{self.session_id}"
            bot_name_mapping[bot_name] = versioned_name
            
            # Clean up old config file
            config_path = os.path.join(bot_dir, "config.json")
            if os.path.exists(config_path):
                try:
                    os.remove(config_path)
                except:
                    pass
            
            try:
                # Register bot account on server
                print(f"  Setting up {versioned_name}...", end=" ", flush=True)
                response = requests.post(
                    f"{self.http_server}/register",
                    params={"username": versioned_name, "is_bot": "true"},
                    timeout=5
                )
                data = response.json()
                
                if "error" in data:
                    print(f"✗ ({data['error']})")
                    continue
                
                token = data.get("token")
                if not token:
                    print(f"✗ (no token received)")
                    continue
                
                # Save credentials to config.json
                config = {
                    "server_url": "ws://localhost:8000/ws",
                    "username": versioned_name,
                    "token": token
                }
                with open(config_path, 'w') as f:
                    json.dump(config, f, indent=4)
                
                print("✓")
            except requests.exceptions.Timeout:
                print(f"✗ (timeout)")
            except Exception as e:
                print(f"✗ ({e})")
        
        # Now launch bots with versioned names
        print(f"\n  Starting bot processes...")
        for bot_name, bot_dir in bots:
            versioned_name = bot_name_mapping[bot_name]
            try:
                # Capture bot output to log files for debugging
                log_path = os.path.join(bot_dir, f"{versioned_name}.log")
                log_file = open(log_path, 'w')
                proc = subprocess.Popen(
                    ["python3", "launcher.py", "--username", versioned_name, "--run-only"],
                    cwd=bot_dir,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    bufsize=1  # Line buffered
                )
                self.bot_processes.append((versioned_name, proc))
                print(f"    {versioned_name}: PID {proc.pid}")
            except Exception as e:
                print(f"    ✗ Failed to launch {versioned_name}: {e}")
        
        # Give bots time to connect
        print("\n  Waiting for bots to connect and authenticate...", end="", flush=True)
        for i in range(15):
            time.sleep(1)
            print(".", end="", flush=True)
        print(" ✓\n")
        
        return bot_name_mapping
    
    def cleanup_bots(self):
        """Clean up bot processes"""
        print("\n  Stopping bot processes...")
        for name, proc in self.bot_processes:
            try:
                proc.terminate()
                proc.wait(timeout=2)
                print(f"    {name}: stopped")
            except:
                proc.kill()
                print(f"    {name}: killed")
    
    async def close(self):
        """Close connection and cleanup"""
        self.cleanup_bots()
        if self.websocket:
            await self.websocket.close()


async def main():
    runner = TournamentRunner()
    bot_name_mapping = None
    try:
        # Launch bots with fresh instances
        bot_name_mapping = runner.launch_bots()
        
        # Connect to server
        await runner.connect()
        
        # Run tournaments with versioned bot names
        lexington = bot_name_mapping["lexington_bot"]
        normal = bot_name_mapping["normal_bot"]
        easy = bot_name_mapping["easy_bot"]
        
        await runner.run_tournament(lexington, normal, 10)
        await runner.run_tournament(lexington, easy, 10)
        
        print(f"\n\n{'='*60}")
        print("FINAL RESULTS")
        print(f"{'='*60}")
        for tournament in runner.tournaments.values():
            tournament.print_summary()
    except KeyboardInterrupt:
        print("\n\nTournament interrupted by user")
    except Exception as e:
        print(f"\nFatal error: {e}")
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(main())
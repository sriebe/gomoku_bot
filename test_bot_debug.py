#!/usr/bin/env python3
"""Debug script to test bot connection and message flow"""

import asyncio
import websockets
import json
import sys

BOARD_SIZE = 19

async def test_bot():
    """Connect as a test bot and log all messages received"""
    print("Connecting to server...")
    ws = await websockets.connect('ws://localhost:8000/ws')
    
    # Authenticate
    print("Authenticating...")
    await ws.send(json.dumps({
        'type': 'authenticate',
        'token': '76e8a0c0-a9a7-4afb-a332-6c834f924be8'  # test_bot_123
    }))
    
    # Listen for messages
    message_count = 0
    while message_count < 20:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(msg)
            msg_type = data.get('type', 'unknown')
            
            # Print message details
            print(f"\n[{message_count}] Type: {msg_type}")
            
            if msg_type == 'game_started':
                print(f"  Game: {data.get('game_id')}")
                print(f"  Color: {data.get('your_color')}")
                print(f"  Opponent: {data.get('player1') if data.get('player2') == 'test_bot_123' else data.get('player2')}")
                
            elif msg_type == 'game_update':
                print(f"  Game: {data.get('game_id')}")
                print(f"  Current turn: {data.get('current_turn')}")
                print(f"  Current player: {data.get('current_player')}")
                print(f"  Game over: {data.get('game_over')}")
                
                # If it's our turn, make a move
                if data.get('current_player') == 'test_bot_123' and not data.get('game_over'):
                    board = data.get('board', [])
                    # Find first empty cell
                    for r in range(BOARD_SIZE):
                        for c in range(BOARD_SIZE):
                            if board[r][c] == 0:
                                print(f"  Making move: ({r}, {c})")
                                await ws.send(json.dumps({
                                    'type': 'make_move',
                                    'row': r,
                                    'col': c
                                }))
                                break
                        else:
                            continue
                        break
                        
            elif msg_type == 'ping':
                await ws.send(json.dumps({'type': 'pong'}))
                print("  (responded with pong)")
                
            elif msg_type == 'error':
                print(f"  Error: {data.get('code')} - {data.get('message')}")
            
            message_count += 1
            
        except asyncio.TimeoutError:
            print("\n[TIMEOUT] No message received for 10 seconds")
            message_count += 1
        except Exception as e:
            print(f"\n[ERROR] {e}")
            break
    
    await ws.close()
    print("\nConnection closed")

if __name__ == "__main__":
    asyncio.run(test_bot())

import json
import requests
import os
import sys
import argparse

CONFIG_FILE = "config.json"

def load_config():
    """Load configuration from config.json"""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {
        "server_url": "ws://localhost:8000/ws",
        "username": "",
        "token": ""
    }

def save_config(config):
    """Save configuration to config.json"""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)
    print(f"\n✓ Configuration saved to {CONFIG_FILE}")

def create_account_from_args(username, server_url="http://localhost:8000"):
    """Create a new bot account from command-line arguments (non-interactive)"""
    config = load_config()
    
    if not username:
        print("✗ Username cannot be empty")
        return False
    
    print(f"\n=== Creating Bot Account ===")
    print(f"Username: {username}")
    print(f"Server: {server_url}")
    
    # Register with server
    try:
        print(f"Connecting to {server_url}...")
        response = requests.post(f"{server_url}/register", params={"username": username, "is_bot": "true"}, timeout=5)
        data = response.json()
        
        if "error" in data:
            # If account already exists, treat as success and load credentials
            if "already exists" in data["error"]:
                print(f"✓ Account already exists, using existing credentials")
                # Credentials should already be in config if we created it before
                if config["username"] == username:
                    return True
                else:
                    print(f"✗ Error: {data['error']}")
                    return False
            else:
                print(f"✗ Error: {data['error']}")
                return False
        
        # Save credentials
        ws_url = server_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        config["server_url"] = ws_url
        config["username"] = data["username"]
        config["token"] = data["token"]
        save_config(config)
        
        print(f"✓ Account created successfully!")
        return True
        
    except requests.exceptions.Timeout:
        print(f"✗ Error: Request timed out after 5 seconds")
        print("  Make sure the server is running!")
        return False
    except requests.exceptions.ConnectionError:
        print(f"✗ Error: Could not connect to server at {server_url}")
        print("  Make sure the server is running!")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

def create_account():
    """Create a new bot account (interactive)"""
    config = load_config()
    
    print("\n=== Create New Bot Account ===")
    
    # Get server URL
    server_url = input(f"Server URL (default: http://localhost:8000): ").strip()
    if not server_url:
        server_url = "http://localhost:8000"
    
    # Get username
    username = input("Enter bot username: ").strip()
    if not username:
        print("✗ Username cannot be empty")
        return
    
    create_account_from_args(username, server_url)

def run_bot():
    """Run the bot with current configuration"""
    config = load_config()
    
    if not config["username"] or not config["token"]:
        print("\n✗ No credentials found!")
        print("  Please create an account first (option 1)")
        return
    
    print("\n=== Running Bot ===")
    print(f"Username: {config['username']}")
    print(f"Server: {config['server_url']}")
    print("\nStarting bot... (Press Ctrl+C to stop)\n")
    
    # Import and run the bot
    try:
        import bot
        bot.SERVER_URL = config["server_url"]
        bot.TOKEN = config["token"]
        bot.USERNAME = config["username"]
        
        import asyncio
        asyncio.run(bot.main())
    except KeyboardInterrupt:
        print("\n\n✓ Bot stopped by user")
    except Exception as e:
        print(f"\n✗ Error running bot: {e}")

def delete_credentials():
    """Delete stored credentials"""
    config = load_config()
    
    if not config["username"] or not config["token"]:
        print("\n✗ No credentials to delete")
        return
    
    print("\n=== Delete Credentials ===")
    print(f"Username: {config['username']}")
    print(f"Token: {config['token']}")
    
    confirm = input("\nAre you sure you want to delete these credentials? (yes/no): ").strip().lower()
    
    if confirm == "yes":
        config["username"] = ""
        config["token"] = ""
        save_config(config)
        print("\n✓ Credentials deleted")
    else:
        print("\n✗ Deletion cancelled")

def view_config():
    """View current configuration"""
    config = load_config()
    
    print("\n=== Current Configuration ===")
    print(f"Server URL: {config['server_url']}")
    print(f"Username: {config['username'] if config['username'] else '(not set)'}")
    print(f"Token: {config['token'] if config['token'] else '(not set)'}")

def main_menu():
    """Display main menu and handle user selection"""
    while True:
        print("\n" + "="*50)
        print("Gomoku Bot Launcher")
        print("="*50)
        
        config = load_config()
        if config["username"] and config["token"]:
            print(f"Current account: {config['username']}")
        else:
            print("Current account: (none)")
        
        print("\n1) Create a new account")
        print("2) Run the bot")
        print("3) Delete existing credentials")
        print("4) View configuration")
        print("5) Exit")
        
        choice = input("\nSelect an option (1-5): ").strip()
        
        if choice == "1":
            create_account()
        elif choice == "2":
            run_bot()
        elif choice == "3":
            delete_credentials()
        elif choice == "4":
            view_config()
        elif choice == "5":
            print("\nGoodbye!")
            sys.exit(0)
        else:
            print("\n✗ Invalid option. Please choose 1-5.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gomoku Bot Launcher")
    parser.add_argument("--username", help="Bot username")
    parser.add_argument("--server", default="http://localhost:8000", help="Server URL (default: http://localhost:8000)")
    parser.add_argument("--create", action="store_true", help="Create account and run bot")
    parser.add_argument("--create-only", action="store_true", help="Create account and exit (don't run bot)")
    parser.add_argument("--run-only", action="store_true", help="Run bot with existing credentials (don't create account)")
    args = parser.parse_args()
    
    # DEBUG: Print what we received
    import sys
    sys.stderr.write(f"[DEBUG] launcher called with: username={args.username}, run_only={args.run_only}\n")
    sys.stderr.flush()
    
    try:
        if args.username:
            # Command-line mode
            config = load_config()
            username = args.username
            
            # DEBUG: Print loaded config
            sys.stderr.write(f"[DEBUG] loaded config: username={config.get('username')}\n")
            sys.stderr.flush()
            
            # Handle different modes
            if args.run_only:
                # Just run the bot with existing credentials
                if config["username"] != username:
                    print(f"✗ No credentials for {username}. Create account first with --create or --create-only.")
                    sys.stderr.write(f"[DEBUG] MISMATCH: {config['username']} != {username}\n")
                    sys.stderr.flush()
                    sys.exit(1)
                sys.stderr.write(f"[DEBUG] Credentials match, running bot\n")
                sys.stderr.flush()
                run_bot()
            elif args.create or args.create_only:
                # Try to create account (handles "already exists" gracefully)
                if not create_account_from_args(username, args.server):
                    sys.exit(1)
                
                # If --create-only flag is set, exit after account creation
                if args.create_only:
                    sys.exit(0)
                
                # Default --create behavior: run bot after account creation
                run_bot()
            else:
                # No flags provided, check if we have credentials for this user
                if config["username"] != username:
                    print(f"✗ No credentials for {username}. Use --create, --create-only, or --run-only.")
                    sys.exit(1)
                
                # Have credentials, run bot
                run_bot()
        else:
            # Interactive menu mode
            main_menu()
    except KeyboardInterrupt:
        print("\n\nGoodbye!")
        sys.exit(0)

from config.env import load_app_env

if __name__ == "__main__":
    load_app_env()
    from adapters.discord.discord_bot import run

    run()

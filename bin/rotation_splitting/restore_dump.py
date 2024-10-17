import logging
import subprocess

logger = logging.getLogger("custom")


def restore_db(filename: str):
    logger.info("Restoring database dump...")
    db_params = {
        'dbname': 'eflips',
        'user': 'linus',
        'password': '1234',
        'host': 'localhost',
        'port': '5432',
    }
    # Construct the psql command
    command = [
        'psql',
        '-h', db_params['host'],
        '-p', db_params['port'],
        '-U', db_params['user'],
        '-d', db_params['dbname'],
        '-f', filename
    ]
    # Set the password environment variable
    env = {
        'PGPASSWORD': "1234"
    }
    # Execute the psql command with stdout and stderr redirected to DEVNULL
    try:
        subprocess.run(command, env=env, stdout=subprocess.DEVNULL, check=True)
        logger.info("Database dump restored.")
    except subprocess.CalledProcessError as e:
        # Handle the error silently or log it if necessary
        logger.info(f'Error while restoring database: {e}')

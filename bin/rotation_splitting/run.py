import os
import subprocess

from bin.rotation_splitting.restore_dump import restore_db

database_url = "postgresql://linus:1234@localhost/eflips"

scenario = 1

file = ('db_single_day.sql')  # ('db.sql')

for percentile in [0, 40, 50, 60, 70, 80]:  # [0, 50, 75, 90]:
    restore_db(file)
    script_path = os.path.join(os.path.dirname(__file__), 'optimise.py')
    try:
        subprocess.run(['python', script_path, f'--scenario_id={scenario}',
                        f'--percentile={percentile}', f'--database_url={database_url}'], check=True)
    except subprocess.CalledProcessError as e:
        print(f"An error occurred while running {script_path}: {e}")

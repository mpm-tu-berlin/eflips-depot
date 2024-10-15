import os
import subprocess

from bin.rotation_splitting.restore_dump import restore_db

database_url = "postgresql://linus:1234@localhost/eflips"

scenario = 2
restore_db()
for percentile in [50, 75, 90]:
    script_path = os.path.join(os.path.dirname(__file__), 'optimise.py')
    try:
        subprocess.run(['python', script_path, f'--scenario_id={scenario}',
                        f'--percentile={percentile}', f'--database_url={database_url}'], check=True)
    except subprocess.CalledProcessError as e:
        print(f"An error occurred while running {script_path}: {e}")
    restore_db()

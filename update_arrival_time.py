import pandas as pd
from datetime import datetime, timedelta

INPUT = 'FLIGHT_randomized.csv'
OUTPUT = 'FLIGHT_arrival_updated.csv'

# Helper to add duration to dep_time
def calculate_arrival(dep_time_str, duration_str):
    # dep_time_str: 'HH:MM:SS.000'
    # duration_str: 'HH:MM'
    dep_time = datetime.strptime(dep_time_str, '%H:%M:%S.%f')
    dur_hours, dur_minutes = map(int, duration_str.split(':'))
    duration = timedelta(hours=dur_hours, minutes=dur_minutes)
    arrival_time = (dep_time + duration).time()
    return arrival_time.strftime('%H:%M:%S.000')

def main():
    df = pd.read_csv(INPUT)
    df['ARRIVAL_TIME'] = [calculate_arrival(dep, dur) for dep, dur in zip(df['DEP_TIME'], df['DURATION'])]
    df.to_csv(OUTPUT, index=False)
    print(f"Updated arrival times written to {OUTPUT}")

if __name__ == "__main__":
    main()

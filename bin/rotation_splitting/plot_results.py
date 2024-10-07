import json

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import ticker

with open('results.json', 'r') as f:
    data = json.load(f)
# Convert data to a pandas DataFrame
df = pd.DataFrame(data)


# Function to compute Pareto front
def pareto_front(df, x_column, y_column, minimize=True):
    """
    Computes the Pareto front from a DataFrame.

    Parameters:
    - df: pandas DataFrame containing the data.
    - x_column: Name of the column to be plotted on the x-axis.
    - y_column: Name of the column to be plotted on the y-axis.
    - minimize: Boolean indicating whether to minimize both objectives.

    Returns:
    - A DataFrame containing the Pareto front points.
    """
    # Sort the DataFrame by x_column
    df_sorted = df.sort_values(by=x_column, ascending=True).reset_index(drop=True)

    pareto = []
    if minimize:
        current_min = float("inf")
        for _, row in df_sorted.iterrows():
            if row[y_column] < current_min:
                pareto.append(row)
                current_min = row[y_column]
    else:
        current_max = -float("inf")
        for _, row in df_sorted.iterrows():
            if row[y_column] > current_max:
                pareto.append(row)
                current_max = row[y_column]
    return pd.DataFrame(pareto)


# Compute the Pareto front (assuming we want to minimize both vehicles and station_count)
pareto = pareto_front(df, "vehicles", "station_count", minimize=True)

# Plotting with swapped axes (vehicles on x-axis, station_count on y-axis)
plt.figure(figsize=(10, 6))
plt.scatter(df["vehicles"], df["station_count"], label="All Points", color="blue")

# Highlight Pareto front
plt.scatter(
    pareto["vehicles"],
    pareto["station_count"],
    label="Pareto Front",
    color="red",
    marker="D",
    s=100,
)

# Optionally, connect Pareto front points
pareto_sorted = pareto.sort_values(by="vehicles")
plt.plot(
    pareto_sorted["vehicles"],
    pareto_sorted["station_count"],
    color="red",
    linestyle="--",
)
plt.gca().yaxis.set_major_locator(ticker.MaxNLocator(integer=True))

plt.title("Pareto Front:\nNumber of Vehicles vs. Electrified Stations")
plt.xlabel("Vehicles")
plt.ylabel("Electrified Stations")
plt.legend()
plt.grid(True)

for entry in data:
    plt.annotate(f"P{entry['percentile']}",
                 (entry['vehicles'], entry['station_count']),
                 textcoords="offset points", xytext=(10, 5), ha='center')

# Save the plot as an image file
plt.savefig('plot.png', dpi=400)
plt.close()  # Close the figure to avoid display warnings
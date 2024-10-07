import json

import matplotlib.pyplot as plt
import numpy as np

with open('results.json', 'r') as f:
    data = json.load(f)
# Extracting the vehicles and station counts
vehicles = np.array([entry['vehicles'] for entry in data])
station_counts = np.array([entry['station_count'] for entry in data])

# Function to find the Pareto front
def pareto_front(vehicles, station_counts):
    pareto_mask = np.ones(len(vehicles), dtype=bool)

    for i in range(len(vehicles)):
        for j in range(len(vehicles)):
            if (vehicles[j] >= vehicles[i]) and (station_counts[j] <= station_counts[i]):
                pareto_mask[i] = False
                break

    return pareto_mask

# Get the Pareto front
pareto_mask = pareto_front(vehicles, station_counts)

# Sorting the data for connecting the dots in a meaningful order
# For example, sort by vehicles in descending order
sorted_indices = np.argsort(-vehicles)
sorted_vehicles = vehicles[sorted_indices]
sorted_station_counts = station_counts[sorted_indices]

# Plotting
plt.figure(figsize=(10, 6))

# Scatter plot for all points
plt.scatter(vehicles, station_counts, color='blue', label='Optimization Results')

# Connect all points with lines
plt.plot(sorted_vehicles, sorted_station_counts, color='blue', linestyle='-', linewidth=1)

# Connect Pareto front points with a solid red line
pareto_vehicles = vehicles[pareto_mask]
pareto_station_counts = station_counts[pareto_mask]

# If there are multiple Pareto points, connect them
if len(pareto_vehicles) > 1:
    # Sort Pareto points for a coherent line
    pareto_sorted_indices = np.argsort(-pareto_vehicles)
    pareto_sorted_vehicles = pareto_vehicles[pareto_sorted_indices]
    pareto_sorted_station_counts = pareto_station_counts[pareto_sorted_indices]
    plt.plot(pareto_sorted_vehicles, pareto_sorted_station_counts, color='red', linestyle='-', linewidth=2, label='Pareto Trend')

# Adding labels and title
plt.xlabel('Vehicles')
plt.ylabel('Electrified Stations')
plt.title('Pareto-Front: Vehicles vs Electrified Stations')
plt.legend()

# Adding grid for better readability
plt.grid(True, linestyle='--', alpha=0.7)

# Annotate points with their percentile
for entry in data:
    plt.annotate(f"P{entry['percentile']}",
                 (entry['vehicles'], entry['station_count']),
                 textcoords="offset points", xytext=(0,10), ha='center')

# Save the plot as an image file
plt.savefig('pareto_front.png', dpi=400)
plt.close()  # Close the figure to avoid display warnings

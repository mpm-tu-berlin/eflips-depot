import os
import pandas as pd
import pickle

for filename in os.listdir():
    if filename != "converter.py":
        i = 0
        old_data = pickle.load(open(filename,"rb"))
        new_data = pd.Series()
        for key, value in old_data.items():
            new_data[str(i)]= value
            i += 900
            new_data[str(i)] = value
            i += 900
            new_data[str(i)] = value
            i += 900
            new_data[str(i)] = value
            i += 900

        pickle.dump(new_data, open(filename, "wb"))

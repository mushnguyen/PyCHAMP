# %%
import os
import sys
import dill
import pandas as pd
import seaborn

from py_champ.models.sd6_model_1f1w import SD6Model4SingleFieldAndWell
from plot_1f1w import (plot_cali_gwrc, plot_cali_gwrc2, plot_crop_ratio, reg_prec_withdrawal)

# Define the working directory
# Malena PC ->
# wd = r"D:\Malena\CHAMP\PyCHAMP\code_20240704\PyCHAMP\examples\Heterogeneity"
# Malena Laptop ->
wd = r"C:\Users\m154o020\CHAMP\PyCHAMP\Summer2024\code_20240705\PyCHAMP\examples\Heterogeneity"

# Add the 'code' directory to sys.path if not already present
if wd not in sys.path:
    sys.path.append(os.path.join(wd, "code"))

# Define a function to add file paths dynamically
def add_file(file_name, alias):
    setattr(paths, alias, os.path.join(wd, file_name))

# Initialize paths as an empty object
paths = type("Paths", (), {})

# Add file paths using the add_file function
init_year = 2011
add_file(f"Inputs_SD6_{init_year+1}_2022.pkl", "input_pkl")
add_file(f"prec_avg_{init_year}_2022.csv", "prec_avg")
add_file(f"Data_SD6_{init_year+1}_2022.csv", "sd6_data")
add_file("calibrated_parameters.txt", "cali_x")

# Load inputs
with open(paths.input_pkl, "rb") as f:
    (
        aquifers_dict,
        fields_dict,
        wells_dict,
        finances_dict,
        behaviors_dict,
        prec_aw_step,
        crop_price_step,
    ) = dill.load(f)

# Load data
prec_avg = pd.read_csv(paths.prec_avg, index_col=[0]).iloc[1:, :]
sd6_data = pd.read_csv(paths.sd6_data, index_col=["year"])

# General model settings
crop_options = ["corn", "others"]

# Function to load parameters from the text file
def load_parameters(file_path):
    x = []
    with open(file_path, 'r') as file:
        lines = file.readlines()
        # print(lines)
        for line in lines:
            if line.startswith("x:"):
                x_str = line.split('[', 1)[1].split(']', 1)[0].strip()
                # print(x_str)
                x = list(map(float, x_str.split()))
    return x


# Load parameters from the calibrated_parameters.txt file
x = load_parameters(paths.cali_x)

for fid in fields_dict:
    fields_dict[fid]["water_yield_curves"]["others"] = [
        x[0],
        x[1],
        x[2],
        x[3],
        x[4],
        0.1186,
    ]
for yr in crop_price_step["finance"]:
    crop_price_step["finance"][yr]["others"] *= x[5]

pars = {
    "perceived_risk": x[6],
    "forecast_trust": x[7],
    "sa_thre": x[8],
    "un_thre": x[9],
}

# %%
# Run the model
m = SD6Model4SingleFieldAndWell(
    pars=pars,
    crop_options=crop_options,
    prec_aw_step=prec_aw_step,
    aquifers_dict=aquifers_dict,
    fields_dict=fields_dict,
    wells_dict=wells_dict,
    finances_dict=finances_dict,
    behaviors_dict=behaviors_dict,
    crop_price_step=crop_price_step,
    init_year=init_year,
    end_year=2022,
    lema_options=(True, "wr_LEMA_5yr", 2013),
    show_step=True,
    seed=67,
)

for i in range(11):
    m.step()

m.end()

# %%
# =============================================================================
# Analyze results
# =============================================================================
data = sd6_data
df_sys, df_agt = m.get_dfs(m)
metrices = m.get_metrices(df_sys, data) # same length

# Save results to CSV files
df_sys_file, df_agt_file = m.save_results()

# Load the saved results
df_sys_results = pd.read_csv(df_sys_file, index_col=0)
df_agt_results = pd.read_csv(df_agt_file, index_col=0)

# Generate the plots using the saved CSV files
output_dir = "plots"
if not os.path.exists(output_dir):
    os.makedirs(output_dir)

timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# =============================================================================
# Plot results
# =============================================================================
# df_sys["GW_st"].plot()
# df_sys["withdrawal"].plot()
# df_sys[["corn", "sorghum", "soybeans", "wheat", "fallow"]].plot()
# df_sys[["Imitation", "Social comparison", "Repetition", "Deliberation"]].plot()

plot_cali_gwrc(df_sys_results.reindex(data.index),
               data,
               metrices,
               prec_avg,
               stochastic=[],
               savefig=os.path.join(output_dir, f"cali_gwrc_{timestamp}.png"))

plot_cali_gwrc2(df_sys_results.reindex(data.index),
               data,
               metrices,
               prec_avg,
               stochastic=[],
               savefig=os.path.join(output_dir, f"cali_gwrc2_{timestamp}.png"))

plot_crop_ratio(df_sys_results.reindex(data.index),
                data,
                metrices,
                prec_avg,
                savefig=os.path.join(output_dir, f"crop_ratio_{timestamp}.png"))

reg_prec_withdrawal(prec_avg,
                     df_sys_results.reindex(data.index),
                     df_sys_nolema=None,
                     data=data,
                     df_sys_list=None,
                     df_sys_nolema_list=None,
                     dot_labels=True,
                     obv_dots=False,
                     savefig=os.path.join(output_dir, f"prec_withdrawal_{timestamp}.png"))
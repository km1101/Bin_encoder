Act as an expert Python developer, cloud architect, and DevOps engineer. I have an existing Python data-processing script named S_Channel.py along with helper files (binstring.py, parameters.csv, table.csv, and multiple bf-*.csv format files) that currently process binary .sbd sensor data files using hardcoded local Windows paths.

I want to transform this codebase into a multi-user web application deployed on Azure App Service. 

Please build the complete solution consisting of the following components:

1. REFACTORED BACKEND (main.py):
- Update the core processing functions (such as process_single_file and a new batch processor function like process_folders_batch) so they do NOT use hardcoded paths.
- They must accept dynamic inputs: a list of input folder paths, an optional custom output directory path, a format definition path, and a parameters file path.
- Maintain all existing logic for decoding binary .sbd files (using binstring.py and bf-*.csv lookup files), handling file sizes (<50 bytes for ENG data, 50-100 bytes for Weather data, 100-900 bytes for Wave data), ensuring CSV headers, and archiving processed files into an 'Obs_archive' folder.
- Execute file processing efficiently using parallel processing (ProcessPoolExecutor).
- improvement for main.py- the headers for eng_data and weather_data are on WEATHER_HEADER = [
    "Dual", "AWS_ID", "Type", "Date", "Time", "Min_Since",
    "Latitude", "Longitude", "Temperature", "Humidity", "Pressure",
    "Sea_Temp", "Wind_Direction", "Wind_Speed", "Gust", "COG",
    "SOG", "Wave_Height", "Wave_Period", "Visibility"
]

ENG_HEADER = [
    "Dual", "AWS_ID", "Type", "Date", "Time", "CR1000_Voltage",  
    "Charge Current", "Logger Temp", "Sat_Ns", "Array Voltage"
]
 put them a a .csv files the same as the wava_data parameters.csv

2. WEB APPLICATION INTERFACE (app.py):
- Build a user-friendly web interface using Streamlit.
- Provide a sidebar configuration where users can input a list of target input folder paths (one per line) directly. Add a user fiendly cooamnd butte to add the folder path or paths to navgate folder explorer. Do NOT use file-uploaders for individual files; processing must happen in the background using folder paths only.
- Provide an optional output destination field where users can specify a custom shared output folder path (falling back to each source folder's directory if left blank).
- Include a "Run Processing" trigger button that executes the batch processing in the background.
- Display real-time processing logs in an expandable view and provide direct download buttons for the generated CSV files (eng_data.csv, weather_data.csv, wave_data.csv) right in the browser.


3. DEPENDENCIES & DEPLOYMENT CONFIGURATION:
- Provide a standard requirements.txt file containing all required libraries (e.g., streamlit, pandas).
- Provide step-by-step instructions on how to configure and deploy this app on Azure App Service (Linux runtime, Python 3.10+, and setting the correct startup command: streamlit run app.py --server.port=8000 --server.address=0.0.0.0).

Please output the full, production-ready code for main.py, app.py, and requirements.txt, along with clear deployment steps.
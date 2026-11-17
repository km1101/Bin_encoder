# Deploying the SBD Batch Processor to Azure App Service (Linux, Python 3.10+)

## 0. Things to understand first

1. **Folder paths are server paths.** The app runs in Azure, so `C:\Users\...\OneDrive` paths are
   not reachable. Put your `.sbd` data on **Azure Files** and mount it into the App Service
   (step 4). Users then type or browse paths such as `/mnt/data/TH2`.
2. **Output behaviour.** If an output folder is given, `eng_data.csv`, `weather_data.csv` and
   `wave_data.csv` are written there (shared). If it is blank, each source folder gets its own CSVs.
3. **Files to deploy together**: `app.py`, `main.py`, `binstring.py`,
   `requirements.txt`, all CSV files in `headers/`, and all `bf-*.csv` files in
   `format_definitions/`.

## 1. Prerequisites

- Azure subscription and the Azure CLI (`az login`)
- Project folder containing the files above

## 2. Create resources

```bash
RG=sbd-rg
LOC=uksouth
PLAN=sbd-plan
APP=sbd-processor-<unique-name>      # must be globally unique
STORAGE=sbdstorage<unique>            # 3-24 lowercase letters/digits

az group create -n $RG -l $LOC
az appservice plan create -g $RG -n $PLAN --is-linux --sku B2
az webapp create -g $RG -p $PLAN -n $APP --runtime "PYTHON:3.11"
```

B2 or higher is recommended (parallel decoding uses several CPU cores).

## 3. App settings and startup command

```bash
az webapp config appsettings set -g $RG -n $APP --settings \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true \
  WEBSITES_PORT=8000 \
  BROWSE_ROOT=/mnt/data

az webapp config set -g $RG -n $APP \
  --web-sockets-enabled true --always-on true \
  --startup-file "streamlit run app.py --server.port=8000 --server.address=0.0.0.0"
```

Streamlit **needs WebSockets enabled**. Optionally append `--server.headless=true` to the
startup command.

## 4. Mount Azure Files for the data

```bash
az storage account create -g $RG -n $STORAGE -l $LOC --sku Standard_LRS
KEY=$(az storage account keys list -g $RG -n $STORAGE --query "[0].value" -o tsv)
az storage share-rm create --storage-account $STORAGE --name sbddata

az webapp config storage-account add -g $RG -n $APP \
  --custom-id sbddata --storage-type AzureFiles \
  --account-name $STORAGE --share-name sbddata \
  --access-key "$KEY" --mount-path /mnt/data
```

Copy your folders (e.g. `TH2`, `Britt2`) into the share with Azure Storage Explorer or AzCopy.
In the app they appear under `/mnt/data/...`. `BROWSE_ROOT=/mnt/data` stops users from
browsing or processing anything outside the share.

## 5. Deploy the code

From the project folder:

```bash
zip -r app.zip . -x "*.git*" "__pycache__/*"
az webapp deploy -g $RG -n $APP --src-path app.zip --type zip
```

(or `az webapp up --name $APP --resource-group $RG --runtime "PYTHON:3.11"`). Azure installs
`requirements.txt` during the build.

## 6. Restrict access (strongly recommended)

The app can move and write files on the share, so don't leave it public:

Portal -> your App Service -> **Authentication** -> Add identity provider -> **Microsoft**
(Entra ID), and set "Require authentication".

## 7. Verify and troubleshoot

- Browse to `https://<APP>.azurewebsites.net`.
- Logs: `az webapp log tail -g $RG -n $APP`
- Blank page / endless "connecting": WebSockets not enabled (step 3).
- "Folder not found": the path isn't under the mounted share, or use the Browse button.
- Very long runs: Azure's front end can drop idle connections after ~4 minutes without
  activity; the live log keeps the session active, but for very large backlogs process
  folder by folder.
- `.sbd` files that fail the CSV write (file locked) stay in the folder and are retried next run.

## Local test

```bash
pip install -r requirements.txt
BROWSE_ROOT=/path/to/data streamlit run app.py        # Windows: set BROWSE_ROOT=C:\data
python main.py /path/to/data/TH2 --output /path/to/out   # command line, no UI
```

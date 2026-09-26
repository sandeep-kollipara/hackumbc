python3 -m venv .venv
source .venv/bin/activate
pip install -r streaming-server/requirements.txt
python streaming-server/manage.py migrate
python streaming-server/manage.py runserver
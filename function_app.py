# function_app.py
import azure.functions as func
from app import app as fastapi_app  # imports your FastAPI instance named `app`

# Use FUNCTION auth in prod; ANONYMOUS is fine behind APIM/AppGW.
app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.FUNCTION)
from flask import Flask, session
app = Flask(__name__)

@app.route("/profile/<user_id>")
def profile(user_id):
    if "user_id" not in session:
        return "auth required", 401
    return get_profile(user_id)

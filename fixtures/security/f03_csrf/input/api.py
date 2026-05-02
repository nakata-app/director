from flask import Flask, request, session
app = Flask(__name__)

@app.route("/transfer", methods=["POST"])
def transfer():
    amount = request.form["amount"]
    to = request.form["to"]
    return do_transfer(session["user_id"], to, amount)

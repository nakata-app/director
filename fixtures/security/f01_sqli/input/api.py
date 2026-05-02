from flask import Flask, request

app = Flask(__name__)

@app.route("/user/<user_id>")
def get_user(user_id):
    sql = "SELECT * FROM users WHERE id=" + user_id
    return execute_sql(sql)

from flask import Flask, render_template, request, redirect, url_for
import sqlite3

app = Flask(__name__)


def get_db():
    connection = sqlite3.connect("moneymap.db")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


# =========================
# HOME
# =========================

@app.route("/")
def home():

    connection = get_db()

    accounts = connection.execute("""
        SELECT *
        FROM accounts
        ORDER BY id DESC
    """).fetchall()

    connection.close()

    return render_template(
        "index.html",
        accounts=accounts
    )


# =========================
# CREATE ACCOUNT
# =========================

@app.route("/create-account", methods=["POST"])
def create_account():

    name = request.form["name"]
    account_type = request.form["type"]
    balance = float(request.form["balance"])

    connection = get_db()

    connection.execute("""
        INSERT INTO accounts (name, type, balance)
        VALUES (?, ?, ?)
    """, (name, account_type, balance))

    connection.commit()
    connection.close()

    return redirect(url_for("home"))


# =========================
# ACCOUNT
# =========================

@app.route("/account/<int:account_id>")
def account(account_id):

    connection = get_db()

    account = connection.execute("""
        SELECT *
        FROM accounts
        WHERE id = ?
    """, (account_id,)).fetchone()

    if account is None:
        connection.close()
        return "Account not found", 404

    categories = connection.execute("""
        SELECT
            categories.id,
            categories.account_id,
            categories.name,
            COALESCE(allocations.amount, 0) AS amount
        FROM categories
        LEFT JOIN allocations
            ON categories.id = allocations.category_id
        WHERE categories.account_id = ?
        ORDER BY categories.id
    """, (account_id,)).fetchall()

    allocated = connection.execute("""
        SELECT COALESCE(SUM(allocations.amount), 0)
        FROM allocations
        JOIN categories
            ON allocations.category_id = categories.id
        WHERE categories.account_id = ?
    """, (account_id,)).fetchone()[0]

    unallocated = account["balance"] - allocated

    connection.close()

    return render_template(
        "account.html",
        account=account,
        categories=categories,
        allocated=allocated,
        unallocated=unallocated
    )


# =========================
# CREATE CATEGORY
# =========================

@app.route("/create-category", methods=["POST"])
def create_category():

    account_id = int(request.form["account_id"])
    name = request.form["name"]

    connection = get_db()

    account = connection.execute("""
        SELECT *
        FROM accounts
        WHERE id = ?
    """, (account_id,)).fetchone()

    if account is None:
        connection.close()
        return "Account not found", 404

    connection.execute("""
        INSERT INTO categories (account_id, name)
        VALUES (?, ?)
    """, (account_id, name))

    category_id = connection.execute("""
        SELECT last_insert_rowid()
    """).fetchone()[0]

    connection.execute("""
        INSERT INTO allocations (category_id, amount)
        VALUES (?, 0)
    """, (category_id,))

    connection.execute("""
        INSERT INTO history
        (account_id, category_id, action, amount, description)
        VALUES (?, ?, ?, ?, ?)
    """, (
        account_id,
        category_id,
        "Created",
        0,
        f"Created category {name}"
    ))

    connection.commit()
    connection.close()

    return redirect(url_for(
        "account",
        account_id=account_id
    ))


# =========================
# UPDATE ALLOCATION
# =========================

@app.route("/update-allocation", methods=["POST"])
def update_allocation():

    category_id = int(request.form["category_id"])
    new_amount = float(request.form["amount"])

    connection = get_db()

    category = connection.execute("""
        SELECT *
        FROM categories
        WHERE id = ?
    """, (category_id,)).fetchone()

    if category is None:
        connection.close()
        return "Category not found", 404

    account = connection.execute("""
        SELECT *
        FROM accounts
        WHERE id = ?
    """, (category["account_id"],)).fetchone()

    allocated_other = connection.execute("""
        SELECT COALESCE(SUM(allocations.amount), 0)
        FROM allocations
        JOIN categories
            ON allocations.category_id = categories.id
        WHERE categories.account_id = ?
        AND allocations.category_id != ?
    """, (
        category["account_id"],
        category_id
    )).fetchone()[0]

    if allocated_other + new_amount > account["balance"]:
        connection.close()
        return "Not enough unallocated money", 400

    old_amount = connection.execute("""
        SELECT amount
        FROM allocations
        WHERE category_id = ?
    """, (category_id,)).fetchone()[0]

    connection.execute("""
        UPDATE allocations
        SET amount = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE category_id = ?
    """, (new_amount, category_id))

    difference = new_amount - old_amount

    connection.execute("""
        INSERT INTO history
        (account_id, category_id, action, amount, description)
        VALUES (?, ?, ?, ?, ?)
    """, (
        category["account_id"],
        category_id,
        "Updated",
        abs(difference),
        f"Allocation changed from ₹{old_amount:.2f} to ₹{new_amount:.2f}"
    ))

    connection.commit()
    connection.close()

    return redirect(url_for(
        "account",
        account_id=category["account_id"]
    ))


# =========================
# SPEND
# =========================

@app.route("/spend", methods=["POST"])
def spend():

    category_id = int(request.form["category_id"])
    amount = float(request.form["amount"])
    description = request.form["description"]

    connection = get_db()

    category = connection.execute("""
        SELECT *
        FROM categories
        WHERE id = ?
    """, (category_id,)).fetchone()

    if category is None:
        connection.close()
        return "Category not found", 404

    allocation = connection.execute("""
        SELECT amount
        FROM allocations
        WHERE category_id = ?
    """, (category_id,)).fetchone()

    if allocation is None:
        connection.close()
        return "Allocation not found", 404

    if amount > allocation["amount"]:
        connection.close()
        return "Not enough money in this category", 400

    new_amount = allocation["amount"] - amount

    connection.execute("""
        UPDATE allocations
        SET amount = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE category_id = ?
    """, (new_amount, category_id))

    connection.execute("""
        INSERT INTO history
        (account_id, category_id, action, amount, description)
        VALUES (?, ?, ?, ?, ?)
    """, (
        category["account_id"],
        category_id,
        "Spent",
        amount,
        description
    ))

    connection.commit()
    connection.close()

    return redirect(url_for(
        "account",
        account_id=category["account_id"]
    ))


# =========================
# TRANSFER
# =========================

@app.route("/transfer", methods=["POST"])
def transfer():

    from_category_id = int(request.form["from_category_id"])
    to_category_id = int(request.form["to_category_id"])
    amount = float(request.form["amount"])

    if from_category_id == to_category_id:
        return "Cannot transfer to the same category", 400

    connection = get_db()

    source = connection.execute("""
        SELECT
            categories.*,
            allocations.amount
        FROM categories
        JOIN allocations
            ON categories.id = allocations.category_id
        WHERE categories.id = ?
    """, (from_category_id,)).fetchone()

    destination = connection.execute("""
        SELECT
            categories.*,
            allocations.amount
        FROM categories
        JOIN allocations
            ON categories.id = allocations.category_id
        WHERE categories.id = ?
    """, (to_category_id,)).fetchone()

    if source is None or destination is None:
        connection.close()
        return "Category not found", 404

    if source["account_id"] != destination["account_id"]:
        connection.close()
        return "Categories must belong to the same account", 400

    if amount > source["amount"]:
        connection.close()
        return "Not enough money in source category", 400

    connection.execute("""
        UPDATE allocations
        SET amount = amount - ?
        WHERE category_id = ?
    """, (amount, from_category_id))

    connection.execute("""
        UPDATE allocations
        SET amount = amount + ?
        WHERE category_id = ?
    """, (amount, to_category_id))

    connection.execute("""
        INSERT INTO history
        (account_id, category_id, action, amount, description)
        VALUES (?, ?, ?, ?, ?)
    """, (
        source["account_id"],
        from_category_id,
        "Transferred",
        amount,
        f"Moved ₹{amount:.2f} from {source['name']} to {destination['name']}"
    ))

    connection.commit()
    connection.close()

    return redirect(url_for(
        "account",
        account_id=source["account_id"]
    ))


# =========================
# DELETE CATEGORY
# =========================

@app.route("/delete-category", methods=["POST"])
def delete_category():

    category_id = int(request.form["category_id"])

    connection = get_db()

    category = connection.execute("""
        SELECT *
        FROM categories
        WHERE id = ?
    """, (category_id,)).fetchone()

    if category is None:
        connection.close()
        return "Category not found", 404

    connection.execute("""
        INSERT INTO history
        (account_id, category_id, action, amount, description)
        VALUES (?, ?, ?, ?, ?)
    """, (
        category["account_id"],
        category_id,
        "Deleted",
        0,
        f"Deleted category {category['name']}"
    ))

    connection.execute("""
        DELETE FROM categories
        WHERE id = ?
    """, (category_id,))

    connection.commit()
    connection.close()

    return redirect(url_for(
        "account",
        account_id=category["account_id"]
    ))


# =========================
# UPDATE ACCOUNT BALANCE
# =========================

@app.route("/update-balance", methods=["POST"])
def update_balance():

    new_balance = float(request.form["balance"])

    connection = get_db()

    # Find account from the referring page's form
    # using the current URL's account is not available,
    # so the account ID should be supplied by the form.

    account_id = int(request.form["account_id"])

    account = connection.execute("""
        SELECT *
        FROM accounts
        WHERE id = ?
    """, (account_id,)).fetchone()

    if account is None:
        connection.close()
        return "Account not found", 404

    allocated = connection.execute("""
        SELECT COALESCE(SUM(allocations.amount), 0)
        FROM allocations
        JOIN categories
            ON allocations.category_id = categories.id
        WHERE categories.account_id = ?
    """, (account_id,)).fetchone()[0]

    if new_balance < allocated:
        connection.close()
        return "Balance cannot be lower than allocated money", 400

    old_balance = account["balance"]

    connection.execute("""
        UPDATE accounts
        SET balance = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (new_balance, account_id))

    connection.execute("""
        INSERT INTO history
        (account_id, category_id, action, amount, description)
        VALUES (?, NULL, ?, ?, ?)
    """, (
        account_id,
        "Balance",
        abs(new_balance - old_balance),
        f"Balance changed from ₹{old_balance:.2f} to ₹{new_balance:.2f}"
    ))

    connection.commit()
    connection.close()

    return redirect(url_for(
        "account",
        account_id=account_id
    ))


# =========================
# ACCOUNT HISTORY
# =========================

@app.route("/history/<int:account_id>")
def history(account_id):

    connection = get_db()

    account = connection.execute("""
        SELECT *
        FROM accounts
        WHERE id = ?
    """, (account_id,)).fetchone()

    if account is None:
        connection.close()
        return "Account not found", 404

    history = connection.execute("""
        SELECT
            history.*,
            categories.name AS category_name
        FROM history
        LEFT JOIN categories
            ON history.category_id = categories.id
        WHERE history.account_id = ?
        ORDER BY history.created_at DESC
    """, (account_id,)).fetchall()

    connection.close()

    return render_template(
        "history.html",
        account=account,
        history=history
    )


# =========================
# CATEGORY HISTORY
# =========================

@app.route("/category-history/<int:category_id>")
def category_history(category_id):

    connection = get_db()

    category = connection.execute("""
        SELECT
            categories.id,
            categories.account_id,
            categories.name,
            accounts.name AS account_name
        FROM categories
        JOIN accounts
            ON categories.account_id = accounts.id
        WHERE categories.id = ?
    """, (category_id,)).fetchone()

    if category is None:
        connection.close()
        return "Category not found", 404

    history = connection.execute("""
        SELECT *
        FROM history
        WHERE category_id = ?
        ORDER BY created_at DESC
    """, (category_id,)).fetchall()

    connection.close()

    return render_template(
        "category_history.html",
        category=category,
        history=history
    )

# =========================
# DELETE ACCOUNT
# =========================

@app.route("/delete-account", methods=["POST"])
def delete_account():

    account_id = int(request.form["account_id"])

    connection = get_db()

    account = connection.execute("""
        SELECT *
        FROM accounts
        WHERE id = ?
    """, (account_id,)).fetchone()

    if account is None:
        connection.close()
        return "Account not found", 404

    connection.execute("""
        DELETE FROM accounts
        WHERE id = ?
    """, (account_id,))

    connection.commit()
    connection.close()

    return redirect(url_for("home"))

if __name__ == "__main__":
    app.run(debug=True)
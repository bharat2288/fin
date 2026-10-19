"""The scope SQL every trend read shares: which book a row is read as, which
accounts count, and the joins that give a row its type. Moved out of app.py
so route modules never import app.py (fin-surfaces 22).
"""

import book_type

# The joins that give a labelled table its type (ty) and that type's parent
# (tp). `{owner}` is the alias of the table carrying type_id.
_TYPE_JOINS = (
    "LEFT JOIN types ty ON {owner}.type_id = ty.id "
    "LEFT JOIN types tp ON ty.parent_id = tp.id"
)



def book_expr(alias: str = "t") -> str:
    """SQL for the book a row is read as: its own, or the default when no one
    has placed it. Scope comes from here and from nowhere else."""
    return f"COALESCE({alias}.book, '{book_type.DEFAULT_BOOK}')"


# Rows on an account kept in the currency the dashboard's figures are in. A
# row on an account in another currency (the rupee account) is whole minor
# units of that currency, and is never added to SGD cents as if it were SGD:
# it is held out of these sums. `s` is the row's statement.
_SGD_ACCOUNT = (
    "s.account_id IN (SELECT id FROM accounts WHERE COALESCE(currency, 'SGD') = 'SGD')"
)


# Rows on an account the household owns: what household spending is counted
# on (ruling 6). Takes the owner as its parameter.
_HOUSEHOLD_ACCOUNT = "s.account_id IN (SELECT id FROM accounts WHERE owner = ?)"

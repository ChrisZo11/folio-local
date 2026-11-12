import pytest
from portfolio.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path/'portfolio.db')


@pytest.fixture
def trade():
    return {'portfolio_id':1,'date':'2025-01-01','type':'buy','symbol':'BBCA',
        'quantity':'100','quantity_unit':'share','price':'1000','fee':'0','tax':'0',
        'currency':'IDR','fx_rate':'1'}

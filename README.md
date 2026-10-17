# STADVDB-MCO1

install these:
pip install pandas sqlalchemy pymysql
pip install flask

to load data: 
run railway_source first
run the etl, next make sure to use this: python etl.py --chunk-size 50000
once it loads into the data warehouse u can test it w some queries just to check

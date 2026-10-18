# STADVDB-MCO1

install these:

pip install pandas sqlalchemy pymysql

pip install flask

python -m pip install pytest pandas sqlalchemy

to load data: 

right click on ur server first, go to edit connection -> advance then copy paste this: OPT_LOCAL_INFILE=1

run railway_source first and load the data there

next, run the etl, next make sure to use this: python etl.py --chunk-size 50000

once it loads into the data warehouse u can test it w some queries just to check

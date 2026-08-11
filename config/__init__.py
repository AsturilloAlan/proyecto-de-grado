# Usamos PyMySQL como driver de MySQL (en vez de mysqlclient) porque es
# una librería pura en Python: se instala con pip sin necesitar compilar
# nada, lo cual evita problemas comunes en Windows. Esta línea le dice a
# Django que use PyMySQL cuando el backend pide el módulo MySQLdb.
import pymysql

pymysql.install_as_MySQLdb()

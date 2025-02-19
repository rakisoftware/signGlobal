PROXY = 'http://username:password@ip:port'

USE_PROXY = True

THREADS = 1 #кол-во потоков

SCHEMAS_TO_CREATE = [1, 1] # скоко схем создавать на каждом акке за 1 прогон мин макс
ATTESTATIONS_TO_CREATE = [1, 1] # тоже самое шо выше токо для аттестаций
PAUSE_BETWEEN_CREATIONS = [5, 15] # пауза между созданием схемы/аттестации

MIN_PAUSE = 5  #пауза между потоками мин и макс
MAX_PAUSE = 10
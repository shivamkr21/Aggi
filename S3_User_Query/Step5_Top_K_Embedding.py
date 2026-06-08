import chromadb
from Step4_QueryVectorDB import QueryVector

import datetime

now = datetime.datetime.now()
print(now)
QueryVector("How is type 2 diabetes treated?",4)
now1 = datetime.datetime.now()
print(now1)
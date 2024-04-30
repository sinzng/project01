#!/usr/bin/env python

import requests
import googlemaps
import time
from typing import List
from geopy.distance import geodesic
import json
from fastapi import FastAPI, HTTPException
import os
import pydantic
from pymongo import mongo_client
from bson.objectid import ObjectId
from pydantic import BaseModel
from getNearPark import nearpark, geocode
from getDogData import  get_breed_ratio, create_pie_chart

pydantic.json.ENCODERS_BY_TYPE[ObjectId] = str

app = FastAPI()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.relpath("./")))
secret_file = os.path.join(BASE_DIR, '../secret.json')

with open(secret_file) as f:
    secrets = json.loads(f.read())


def get_secret(setting, secrets=secrets):
    try:
        return secrets[setting]
    except KeyError:
        errorMsg = "Set the {} environment variable.".format(setting)
        return errorMsg


HOSTNAME = get_secret("Local_Mongo_Hostname")
USERNAME = get_secret("Local_Mongo_Username")
PASSWORD = get_secret("Local_Mongo_Password")

client = mongo_client.MongoClient(f'mongodb://{USERNAME}:{PASSWORD}@{HOSTNAME}:27017/')



@app.get('/')
async def healthcheck():
    return "OK"

@app.get('/getmongo')
async def getMongo(type:str):
    # MongoDB에 연결
    db = client["projectjh"]
    collection = db[type]
    data = list(collection.find({}, {"_id":0}).limit(10))
    print(data)
    return data

# 주소 -> 좌표(위도,경도) 전환 API 활용
api_key = 'AIzaSyCP29VXXIjK2E7rfNK4rO5JURBAh50mrqA'
maps = googlemaps.Client(key=api_key)
def get_parkRanking(api_key, query):
    parks_data = []
    next_page_token = None

    while True:
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {
            'query': query,
            'key': api_key,
            'language': 'ko',
            'pagetoken': next_page_token  # 첫 요청에서는 None이므로 파라미터가 무시됨
        }

        response = requests.get(url, params=params)
        results = response.json()

        for place in results['results']:
            parks_data.append({
                'name': place.get('name'),
                'address': place.get('formatted_address'),
                'lat': place.get('geometry')['location']['lat'],
                'lng': place.get('geometry')['location']['lng']
            })

        next_page_token = results.get('next_page_token')
        if not next_page_token or 'error_message' in results:
            break
        time.sleep(2)  # API 요구사항에 따라 토큰 간 2초 대기

    return parks_data
def save_to_json(data, filename):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


# Pydantic 모델을 사용하여 응답 데이터 형식 정의
class ParkData(BaseModel):
    name: str
    address: str
    lat: float
    lng: float

def savetomongodb(data,collection_name):
    # MongoDB에 연결
    db = client["projectjh"]
    collection = db[collection_name]
    try:
        # MongoDB에 데이터 삽입
        result = collection.insert_many(data)

        # 삽입된 데이터 조회
        inserted_ids = result.inserted_ids
        inserted_data = list(collection.find({"_id": {"$in": inserted_ids}}, {"_id": 0}).limit(5))

        return {"code":200,"type":collection_name,"data":inserted_data}
    except Exception as e:
        # 그 외의 예외가 발생하면 서버 오류로 간주합니다.
        raise HTTPException(status_code=500, detail=str(e))




@app.get('/parks')
async def get_all_parks(location: str, type:str):
    # MongoDB에 연결
    # 서울시 각 구의 동들을 딕셔너리로 정의, 나중에 데이터 많아지면 파일로 저장하든가 말든가ㅣ...ㅎ ㅎ
    areas_by_location = {
        "강남구": ["신사동", "논현동", "압구정동", "청담동", "삼성동", "대치동", "역삼동",
                "도곡동", "개포동", "세곡동", "일원동", "수서동"],
        "서초구": ['잠원동', '반포동', '서초동','방배동', '양재동', '내곡동','우면동']
    }
    # 입력한 지역에 따라 해당하는 동들의 리스트 가져오기
    areas = areas_by_location.get(location, [])

    all_parks = []

    # 입력한 지역과 함수 내의 지역을 합쳐서 쿼리를 구성하여 공원 정보를 가져옴
    for area in areas:
        query = f'공원 in {location} {area}'
        park_info = get_parkRanking(api_key, query)

        # 리스트로 변환하여 추가
        park_list = []
        for park in park_info:
            park_list.append({
                "name": park["name"],
                "address": park["address"],
                "lat": park["lat"],
                "lng": park["lng"]
            })
        all_parks.extend(park_list)

    # 주소를 추출하여 중복 제거
    unique_addresses = list({park["address"]: park for park in all_parks}.values())

    # MongoDB에 데이터 저장하고 결과 반환
    return savetomongodb(unique_addresses,type)

@app.get('/nearparks')
async def getNearParks(location: str):
    nearest_parks = nearpark(location)
    type = "nearpark"

    output_data = []
    for i, park_data in enumerate(nearest_parks, 1):
        park_name = park_data["park"]
        distance = park_data["distance"]
        # print(f"{i}. {park_name}: {distance:.2f} km")
        output_data.append({"park": park_name, "distance": round(distance, 2)})
    return output_data

key = get_secret("apikey_dog")
@app.get("/dogdata")
async def get_dog_data(start:int, end:int, region:str):
    collection_name= "dogs"
    url = f'http://211.237.50.150:7080/openapi/{key}/json/Grid_20210806000000000612_1/{start}/{end}?CTPV=서울특별시&SGG={region}&LVSTCK_KND=개'
    # 해당 URL로 요청을 보내고 응답을 받아옴
    response = requests.get(url)
    data = response.json()
    # "row" 키에 해당하는 값을 추출하여 MongoDB에 저장
    row_data = data.get("Grid_20210806000000000612_1", {}).get("row", [])
    # "row" 키에 해당하는 값을 추출하여 특정 필드만 저장
    # row_data = []
    # for document in data.get("Grid_20210806000000000612_1", {}).get("row", []):
    #     # 특정 필드만 추출하여 새로운 딕셔너리에 저장
    #     filtered_document = {
    #         "CTPV": document.get("CTPV"),
    #         "SGG": document.get("SGG"),
    #         "SPCS": document.get("SPCS"),
    #         "CNT": document.get("CNT")
    #     }
    #     # 새로운 딕셔너리를 리스트에 추가
    #     row_data.append(filtered_document)

    return row_data

# @app.get("/dogbreed")
# async def get_dogbreeds(sgg:str):
#     result = get_breed()
#     max = get_max_breed_ratio()
#     return (result, max)
@app.get("/getratio")
async def get_ratio(region:str, type:str):
    breed_ratio = get_breed_ratio(region)
    breed_list = [{"breed": breed, "ratio": ratio} for breed, ratio in breed_ratio.items()]
    
    
    return savetomongodb(breed_list, type)
1
@app.get("/createchart")
async def get_chart(region:str):
    db = client["projectjh"]
    collection = db["image_paths"]
    # 이미지 파일 생성 및 경로 가져오기
    image_path = create_pie_chart(region)
    collection.insert_one({"image_path":image_path})

    return {"code": 200, "data": {"image_path": image_path}}
# -*- coding: utf-8 -*-
"""
KBID 입찰 결과 추적기
파일명: kbid_Results.py
"""

import time
import re
import os
import gspread
import traceback
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

class KbidConfig:
    LOGIN_URL = "https://www.kbid.co.kr/login/common_login.htm"
    SEARCH_URL_TEMPLATE = "https://www.kbid.co.kr/search/index.htm?mid=lge123&txtFindWordTop={}"
    CLIENT_SECRETS_FILE = "client_secrets.json"
    TOKEN_FILE = "token.json"
    SPREADSHEET_NAME = "입찰관리"
    SELECTORS = {
        "login_check": ["//*[contains(text(), '로그아웃')]", "//a[contains(@href, 'logout')]"],
        "search_results": "#listBody1 tr",
        "detail_title": "td.h_tit",
        "th_td_pair": "(//th[contains(text(), '{label}')])[last()]/following-sibling::td[1]"
    }

class GoogleSheetsManager:
    def __init__(self):
        self.client = gspread.oauth(credentials_filename=KbidConfig.CLIENT_SECRETS_FILE, authorized_user_filename=KbidConfig.TOKEN_FILE)
        self.sheet = self.client.open(KbidConfig.SPREADSHEET_NAME)
        self.ws = self.sheet.worksheet("투찰준비")

    def get_result_tasks(self):
        """'결과확인' 상태인 공고 목록을 가져옵니다."""
        all_data = self.ws.get_all_records()
        tasks = []
        for idx, row in enumerate(all_data, start=2):
            if row.get("투찰상태") == "결과확인":
                announcement_name = row.get("공고명", "")
                # 줄바꿈과 '투찰마감' 제거
                cleaned_name = announcement_name.replace('\n', ' ').replace('투찰마감', '').strip()
                tasks.append({
                    "row_idx": idx,
                    "name": cleaned_name,  # 수정된 공고명 사용
                    "num": row.get("공고번호", ""),
                    "data": row
                })
        return tasks

    def update_row(self, row_idx, data_dict):
        """특정 행의 결과 데이터를 업데이트합니다."""
        headers = self.ws.row_values(1)
        row_to_save = []
        for h in headers:
            clean_h = h.replace("*", "").strip()
            row_to_save.append(data_dict.get(clean_h, ""))
        
        end_col = chr(64 + len(headers)) if len(headers) <= 26 else "Z"
        range_name = f"A{row_idx}:{end_col}{row_idx}"
        self.ws.update(range_name, [row_to_save])

class ResultParser:
    def __init__(self, driver):
        self.driver = driver

    def _find_summary_value(self, label):
        try:
            xpath = f"//th[contains(., '{label}')]/following-sibling::td[1] | //td[contains(., '{label}')]/following-sibling::td[1]"
            elements = self.driver.find_elements(By.XPATH, xpath)
            for elem in elements:
                text = elem.text.strip()
                if text: return text
            return ""
        except: return ""

    def parse_results(self):
        """결과 페이지에서 데이터 추출"""
        result_data = {"AIR채호원 사정률": "", "AIR채호원 순위": "", "1등 낙찰율": "", "사정률": "", "참여 업체수": ""}
        
        # 상단 요약 정보
        result_data["사정률"] = self._find_summary_value("사정률")
        
        # 테이블 파싱
        try:
            table = self.driver.find_element(By.XPATH, "//table[.//th[contains(., '순위')]]")
            headers = [h.text.strip() for h in table.find_elements(By.XPATH, ".//th")]
            rows = []
            for row_elem in table.find_elements(By.XPATH, ".//tr[td]"):
                cells = [c.text.strip() for c in row_elem.find_elements(By.XPATH, ".//td")]
                if cells: rows.append(dict(zip(headers, cells)))
            
            if rows:
                result_data["참여 업체수"] = str(len(rows))
                # 1등 낙찰율
                first_row = rows[0]
                for k, v in first_row.items():
                    if "투찰률" in k or "투찰율" in k: result_data["1등 낙찰율"] = v; break
                
                # AIR채호원 검색
                target = "AIR채호원".replace(" ", "")
                for r in rows:
                    comp_name = next((v for k, v in r.items() if "상호" in k or "업체" in k), "").replace(" ", "")
                    if target in comp_name:
                        result_data["AIR채호원 순위"] = next((v for k, v in r.items() if "순위" in k), "")
                        result_data["AIR채호원 사정률"] = next((v for k, v in r.items() if "투찰" in k or "사정" in k), "")
                        break
        except: pass
        return result_data

class ResultCrawler:
    def __init__(self):
        self.gs = GoogleSheetsManager()
        options = Options()
        options.add_argument("--incognito")
        options.add_argument('--disable-blink-features=AutomationControlled')
        self.driver = webdriver.Chrome(options=options)

    def run(self):
        tasks = self.gs.get_result_tasks()
        if not tasks: print("✅ 결과 확인이 필요한 공고가 없습니다."); return

        # 로그인 (수동)
        self.driver.get(KbidConfig.LOGIN_URL)
        print("🔑 로그인을 완료해주세요...")
        while "login" in self.driver.current_url: time.sleep(1)

        parser = ResultParser(self.driver)
        for task in tasks:
            print(f"\n🔍 결과 추적: {task['name']}")
            search_term = task['num'] or task['name']
            self.driver.get(KbidConfig.SEARCH_URL_TEMPLATE.format(search_term))
            time.sleep(3)
            
            try:
                # 결과공고 섹션 대기 및 클릭
                btn = WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//b[contains(text(), '최근 결과공고')]/ancestor::div[1]//a")))
                self.driver.execute_script("arguments[0].click();", btn)
                time.sleep(3)
                
                if len(self.driver.window_handles) > 1:
                    self.driver.switch_to.window(self.driver.window_handles[-1])
                
                res = parser.parse_results()
                task['data'].update(res)
                self.gs.update_row(task['row_idx'], task['data'])
                print(f"✅ 결과 업데이트 완료: {res}")
                
                if len(self.driver.window_handles) > 1:
                    self.driver.close()
                    self.driver.switch_to.window(self.driver.window_handles[0])
            except:
                print(f"❌ 결과를 찾을 수 없음: {search_term}")
        
        self.driver.quit()
        print("\n🏁 결과 처리 완료")

if __name__ == "__main__":
    ResultCrawler().run()

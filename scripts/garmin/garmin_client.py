import logging
import os
from enum import Enum, auto
import requests

import garth


from .garmin_url_dict import GARMIN_URL_DICT

logger = logging.getLogger(__name__)


class GarminClient:
  def __init__(self, email, password, auth_domain, newest_num):
        self.auth_domain = auth_domain
        self.email = email
        self.password = password
        self.garthClient = garth
        self.newestNum = int(newest_num)
        # 新增: 从环境变量读取 garth token (绕过 MFA)
        self.garth_token = os.getenv("GARMIN_GARTH_TOKEN", "")
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.88 Safari/537.36",
            "origin": GARMIN_URL_DICT.get("SSO_URL_ORIGIN"),
            "nk": "NT"
        }

  ## 登录装饰器
  def login(func):
    def ware(self, *args, **kwargs):
      try:
         garth.client.username
      except Exception:
        logger.warning("Garmin is not logging in or the token has expired.")

        # 优先配置域名 (国区必须在 loads/login 之前)
        if self.auth_domain and str(self.auth_domain).upper() == "CN":
          self.garthClient.configure(domain="garmin.cn")

        # 优先使用 token 加载 (绕过 MFA)
        token_loaded = False
        if self.garth_token:
          try:
            self.garthClient.client.loads(self.garth_token)
            # 校验 token: 只检查 oauth1_token 是否已设置 (不调 profile 接口, 避免误判)
            assert self.garthClient.client.oauth1_token is not None
            assert self.garthClient.client.oauth2_token is not None
            token_loaded = True
            logger.info("Garmin logged in via garth token (no MFA required).")
          except Exception as token_err:
            logger.warning(f"Garth token load failed: {token_err}, falling back to password login.")

        # token 不可用则回退到账号密码登录 (会触发 MFA, 在 GH Actions 里会失败)
        if not token_loaded:
          self.garthClient.login(self.email, self.password)

        # del self.garthClient.sess.headers['User-Agent']
        del self.garthClient.client.sess.headers['User-Agent']

      return func(self, *args, **kwargs)
    return ware

  @login
  def download(self, path, **kwargs):
     return self.garthClient.download(path, **kwargs)

  @login
  def connectapi(self, path, **kwargs):
      return self.garthClient.connectapi(path, **kwargs)


  ## 获取运动
  def getActivities(self, start:int, limit:int):

     params = {"start": str(start), "limit": str(limit)}
     activities =  self.connectapi(path=GARMIN_URL_DICT["garmin_connect_activities"], params=params)
     return activities;

  ## 获取所有运动
  def getAllActivities(self):
    all_activities = []
    start = 0
    while(True):
      activities = self.getActivities(start=start, limit=100)
      if len(activities) > 0:
         all_activities.extend(activities)
      else:
         return all_activities
      start += 100

  ## 下载原始格式的运动
  def downloadFitActivity(self, activity):
    download_fit_activity_url_prefix = GARMIN_URL_DICT["garmin_connect_fit_download"]
    download_fit_activity_url = f"{download_fit_activity_url_prefix}/{activity}"
    response = self.download(download_fit_activity_url)
    return response

  @login
  def upload_activity(self, activity_path: str):
    """Upload activity in fit format from file."""
    file_base_name = os.path.basename(activity_path)
    file_extension = file_base_name.split(".")[-1]
    allowed_file_extension = (
        file_extension.upper() in ActivityUploadFormat.__members__
    )

    if allowed_file_extension:
       try:
        with open(activity_path, 'rb') as file:
          file_data = file.read()
          fields = {
              'file': (file_base_name, file_data, 'text/plain')
          }

          url_path = GARMIN_URL_DICT["garmin_connect_upload"]
          upload_url = f"https://connectapi.{self.garthClient.client.domain}{url_path}"
          self.headers['Authorization'] = str(self.garthClient.client.oauth2_token)
          response = requests.post(upload_url, headers=self.headers, files=fields)
          res_code = response.status_code
          result = response.json()
          uploadId =  result.get("detailedImportResult").get('uploadId')
          isDuplicateUpload = uploadId == None or uploadId == ''
          if res_code == 202 and not isDuplicateUpload:
              status = "SUCCESS"
          elif res_code == 409 and result.get("detailedImportResult").get("failures")[0].get('messages')[0].get('content') == "Duplicate Activity.":
              status = "DUPLICATE_ACTIVITY"
       except Exception as e:
            print(e)
            status = "UPLOAD_EXCEPTION"
       finally:
            return status
    else:
        return "UPLOAD_EXCEPTION"


class ActivityUploadFormat(Enum):
  FIT = auto()
  GPX = auto()
  TCX = auto()

class GarminNoLoginException(Exception):
    """Raised when rate limit is exceeded."""

    def __init__(self, status):
        """Initialize."""
        super(GarminNoLoginException, self).__init__(status)
        self.status = status

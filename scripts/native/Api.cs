using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Web.Script.Serialization;

namespace DafeiyuNative
{
    internal sealed class Api
    {
        private readonly string _baseUrl;
        private readonly string _tokenPath;
        private readonly JavaScriptSerializer _json = new JavaScriptSerializer();

        public Api(int port, string root)
        {
            _baseUrl = "http://127.0.0.1:" + port;
            _tokenPath = Path.Combine(root, "data", "ui-token");
        }

        public string BaseUrl
        {
            get { return _baseUrl; }
        }

        public string Token
        {
            get
            {
                try
                {
                    if (!File.Exists(_tokenPath)) return "";
                    return File.ReadAllText(_tokenPath, Encoding.ASCII).Trim();
                }
                catch
                {
                    return "";
                }
            }
        }

        public Dictionary<string, object> Get(string path)
        {
            return Send("GET", path, null);
        }

        public Dictionary<string, object> Put(string path, object body)
        {
            return Send("PUT", path, body);
        }

        private Dictionary<string, object> Send(string method, string path, object body)
        {
            string token = Token;
            if (token.Length == 0) throw new InvalidOperationException("控制台 token 还没生成");

            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(_baseUrl + path);
            request.Method = method;
            request.Timeout = 8000;
            request.ReadWriteTimeout = 8000;
            request.Headers["X-UI-Token"] = token;
            request.UserAgent = "DafeiyuNative/1.0";

            if (body != null)
            {
                string payload = _json.Serialize(body);
                byte[] bytes = Encoding.UTF8.GetBytes(payload);
                request.ContentType = "application/json; charset=utf-8";
                request.ContentLength = bytes.Length;
                using (Stream stream = request.GetRequestStream())
                {
                    stream.Write(bytes, 0, bytes.Length);
                }
            }

            try
            {
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                using (StreamReader reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                {
                    string text = reader.ReadToEnd();
                    object parsed = _json.DeserializeObject(text);
                    return J.Obj(parsed);
                }
            }
            catch (WebException ex)
            {
                string detail = ex.Message;
                HttpWebResponse response = ex.Response as HttpWebResponse;
                if (response != null)
                {
                    try
                    {
                        using (StreamReader reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                        {
                            string text = reader.ReadToEnd();
                            Dictionary<string, object> error = J.Obj(_json.DeserializeObject(text));
                            string message = J.Str(error["error"], "");
                            if (message.Length > 0) detail = message;
                        }
                    }
                    catch { }
                    finally { response.Close(); }
                }
                throw new InvalidOperationException(detail);
            }
        }
    }
}

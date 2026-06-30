---
name: api-design
description: RESTful API 统一接口设计规范，用户询问接口、REST、API开发时严格遵循本标准
---

# API设计技能

## 这是什么？

当用户问API设计、接口设计、RESTful相关问题时，你按这个标准来。

## API设计原则

### 1. URL用名词，不用动词

```
# 不好
GET /api/getUsers
POST /api/createUser

# 好
GET /api/users        # 获取用户列表
POST /api/users       # 创建用户
GET /api/users/123    # 获取id为123的用户
```

### 2. 用HTTP方法表示操作

```
GET    = 查看/获取
POST   = 创建
PUT    = 更新
DELETE = 删除
```

### 3. 返回格式统一

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "id": 123,
    "name": "Tom"
  }
}
```

### 4. 错误返回格式

```json
{
  "code": 400,
  "message": "参数错误",
  "error": "name不能为空"
}
```

### 5. 版本号放URL里

```
/api/v1/users
/api/v2/users
```

## 怎么回复？

看用户设计的API，检查是否符合上面的原则，指出问题和改进建议。
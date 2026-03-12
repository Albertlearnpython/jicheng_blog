from django.contrib.auth import get_user_model

User = get_user_model()

# 查找admin用户并设置密码
try:
    user = User.objects.get(username='admin')
    user.set_password('admin123')
    user.save()
    print('密码设置成功！')
except User.DoesNotExist:
    print('用户不存在')
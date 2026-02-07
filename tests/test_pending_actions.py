from db import set_pending_action, get_pending_action, clear_pending_action

def test_pending_action_expirada_nao_quebra(user_id):
    set_pending_action(user_id, "delete_launch", {"launch_id": 123}, minutes=-1)
    assert get_pending_action(user_id) is None
    clear_pending_action(user_id)

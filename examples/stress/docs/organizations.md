# Organizations

Roles are `owner`, `admin`, and `member`. Any other role raises `NimbusValidationError`.

```python
org = client.organizations.create(name=org_name)
membership = client.organizations.add_member(org.id, user_id=user.id, role=role)
```

`add_member` requires both records to exist already. Create the user with `client.users.create` first. See `users.md`.

`app.organizations.add_user_to_organization(client, *, org_name, user_name, user_email, role)` creates the organization, creates the user with empty metadata, adds the membership, and returns:

```python
{
    "organization_id": org.id,
    "user_id": user.id,
    "role": membership.role,
}
```

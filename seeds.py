from database.session import engine
from models.user import User
from models.document import Document
from auth import hash_password

from sqlmodel import Session, select


def create_users():

    with Session(engine) as session:

        users = [
            {
                "username": "admin",
                "email": "admin@sendit.com",
                "password": "admin12345",
                "full_name": "SendIt Admin",
                "role": "admin"
            },
            {
                "username": "manager",
                "email": "manager@sendit.com",
                "password": "manager12345",
                "full_name": "SendIt Manager",
                "role": "manager"
            },
            {
                "username": "staff",
                "email": "staff@sendit.com",
                "password": "staff12345",
                "full_name": "SendIt Staff",
                "role": "staff"
            }
        ]

        for data in users:

            existing = session.exec(
                select(User).where(
                    User.username == data["username"]
                )
            ).first()

            if existing:
                print(
                    f"{data['username']} already exists"
                )
                continue

            user = User(
                username=data["username"],
                email=data["email"],
                hashed_password=hash_password(
                    data["password"]
                ),
                full_name=data["full_name"],
                role=data["role"]
            )

            session.add(user)

        session.commit()

        print("Seed users created successfully.")


if __name__ == "__main__":
    create_users()
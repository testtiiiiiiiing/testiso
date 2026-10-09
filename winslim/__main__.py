from .base import elevate_if_needed
from .studio import App


def main():
    elevate_if_needed()
    App().mainloop()


if __name__ == "__main__":
    main()

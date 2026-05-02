def used():
    return 42

def dead_helper(x):
    # never called anywhere in the file
    return x * 2

def main():
    print(used())

if __name__ == "__main__":
    main()

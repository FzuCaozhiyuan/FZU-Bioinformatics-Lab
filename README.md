# FZU Bioinformatics Lab
We build a feature selection framework to solve the feature selection problem in multi-omics dataset. Our framework is implemented based on code from [FSNS](https://github.com/NanxuGong/feature-selection-via-autoregreesive-generation).
## Framework
![framework](image/framework.png)
## Implementation
### Step 1: download the data: 
The dataset can be found in [kaggle](https://www.kaggle.com/datasets/userfzuczy/tcga-multi-omics).
Kindly download the dataset from the link and cp it to this path './data/'.


### Step 2: collect the training data
```
python3 code/baseline/automatic_feature_selection_gen.py --name tcga --choice REDUNDANCY_CHOICE --unsupervised IS_UNSUPERVISED
```
### Step 3: generate the optimal feature subset
```
python3 code/ours/train_controller.py --method_name bilstmVae --task_name tcga --gen_num 25 --batch_size 64 --epochs 1000 --lr 0.0001
```


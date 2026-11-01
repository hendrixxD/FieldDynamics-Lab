# Model Evaluation

## Evaluation per model

| Model | How to evaluate | Metrics |
| --- | --- | --- |
| Season clustering (unsupervised) | Check clusters are distinct and stable, then confirm they match known years (does a documented drought year land in the "dry" cluster?) | Silhouette score, Davies-Bouldin index, stability across reruns |
| Vulnerability model (predicts NDVI or stress) | Leave-one-year-out and spatial block cross-validation; baseline (linear) vs LightGBM side by side | RMSE, MAE, R² |
| Uncertainty on that model | Check that prediction intervals actually contain the true values at the stated rate | Interval coverage (an 80% interval should contain about 80% of results) |
| Water balance (FAO-56) | Compare computed soil water trend against SMAP soil moisture over the same seasons | Correlation, bias |
| Rotation scoring (rules) | Sensitivity analysis: nudge weights and inputs, confirm rankings don't flip wildly; check outputs against IAR Samaru trial findings | Rank stability, agreement with literature |
| Whole system | Backtest: run the engine on a known bad season and show it would have flagged the risk | Pass or fail, shown as one chart |

## Tools

| Need | Tool |
| --- | --- |
| Metrics and cross-validation | scikit-learn (GroupKFold, LeaveOneGroupOut, metrics) |
| Spatial blocking | Assign each pixel a block ID with geopandas, then use GroupKFold |
| Prediction intervals | LightGBM quantile regression, or bootstrapping |
| Explainability checks | SHAP (do the top features make physical sense?) |
| Sensitivity analysis | SALib, or a simple loop in Python |
| Experiment log | A plain CSV or JSON log in the repo. Skip MLflow; it's setup time you don't have |
| Charts | matplotlib in notebooks; Chart.js for the backtest in the app |
